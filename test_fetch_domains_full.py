"""
Fetch all advertiser domains + app metadata from vx_overview.
Paginates in batches of 10K rows, up to a 500K hard limit, then deduplicates.

Run from project root:
    python3 test_fetch_domains_full.py
"""
import csv
import json
import os
import requests
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL      = os.environ["LOOKER_BASE_URL"]
LOOKER_CLIENT_ID     = os.environ["LOOKER_CLIENT_ID"]
LOOKER_CLIENT_SECRET = os.environ["LOOKER_CLIENT_SECRET"]

LOOKBACK_DAYS = 30
BATCH_SIZE    = 10_000
HARD_LIMIT    = 500_000
OUT_FILE      = "output/test_domains_full.csv"

FIELDS = [
    "vx_overview.adomain",
    "advertiser_app_metadata.title",
    "advertiser_app_metadata.app_description",
    "advertiser_app_metadata.average_rating",
    "advertiser_app_metadata.content_rating",
    "advertiser_app_metadata.content_advisories",
    "vx_overview.unified_ad_spend",
    "vx_overview.views",
]

os.makedirs("output", exist_ok=True)


def get_token():
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}"
    )
    resp.raise_for_status()
    print("✓ Authenticated")
    return resp.json()["access_token"]


def headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def create_query(token, offset):
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries",
        headers=headers(token),
        json={
            "model":   "vx_analytics",
            "view":    "vx_overview",
            "fields":  FIELDS,
            "filters": {
                "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
                "vx_overview.views":      ">0",
            },
            "sorts":   ["vx_overview.unified_ad_spend desc"],
            "limit":   str(BATCH_SIZE),
            "offset":  offset,
        },
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()["id"]


def fetch_batch(token, offset):
    import time

    # Step 1 — create a saved query and get its ID
    query_id = create_query(token, offset)

    # Step 2 — submit as async task
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/query_tasks",
        headers=headers(token),
        params={"cache": "false"},
        json={"query_id": query_id, "result_format": "json"},
        timeout=30
    )
    resp.raise_for_status()
    task_id = resp.json()["id"]

    # Step 3 — poll until complete (up to 20 min)
    for attempt in range(240):
        time.sleep(5)
        status_resp = requests.get(
            f"{LOOKER_BASE_URL}/api/4.0/query_tasks/{task_id}",
            headers=headers(token),
            timeout=30
        )
        status_resp.raise_for_status()
        status = status_resp.json().get("status", "")
        if attempt % 6 == 0:
            print(f"    [{attempt * 5}s] status: {status}")
        if status == "complete":
            break
        if status in ("error", "killed", "expired"):
            raise RuntimeError(f"Query task failed with status: {status}")

    # Step 4 — fetch results
    results_resp = requests.get(
        f"{LOOKER_BASE_URL}/api/4.0/query_tasks/{task_id}/results",
        headers=headers(token),
        timeout=120
    )
    results_resp.raise_for_status()
    return results_resp.json()


if __name__ == "__main__":
    token    = get_token()
    all_rows = []
    offset   = 0
    batch_n  = 0

    print(f"Fetching from vx_overview (last {LOOKBACK_DAYS} days, views>0, up to {HARD_LIMIT:,} rows)...")

    while offset < HARD_LIMIT:
        batch_n += 1
        print(f"  Batch {batch_n}: rows {offset:,} → {offset + BATCH_SIZE:,}...", end=" ", flush=True)

        rows = fetch_batch(token, offset)
        print(f"{len(rows):,} returned")

        if not rows:
            print("  No more rows — done.")
            break

        all_rows.extend(rows)
        offset += len(rows)

        if len(rows) < BATCH_SIZE:
            print("  Last batch (fewer rows than batch size) — done.")
            break

    print(f"\n✓ Total rows fetched : {len(all_rows):,}")

    if not all_rows:
        print("No rows returned — check Looker fields/filters.")
        exit(0)

    # Deduplicate by adomain (keep highest-spend row per domain)
    seen      = {}
    key_field = "vx_overview.adomain"
    spend_field = "vx_overview.unified_ad_spend"

    for row in all_rows:
        domain = (row.get(key_field) or "").strip().lower()
        if not domain:
            continue
        existing_spend = float(seen[domain].get(spend_field) or 0) if domain in seen else -1
        this_spend     = float(row.get(spend_field) or 0)
        if this_spend > existing_spend:
            seen[domain] = row

    deduped = list(seen.values())
    print(f"✓ After dedup by adomain : {len(deduped):,} unique domains")
    print(f"  Dropped duplicates     : {len(all_rows) - len(deduped):,}")

    # Write CSV
    fieldnames = list(all_rows[0].keys())
    with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(deduped)

    print(f"✓ Saved → {OUT_FILE}")
    print("\nFirst 5 rows:")
    for r in deduped[:5]:
        print(json.dumps(r, indent=2, default=str))
