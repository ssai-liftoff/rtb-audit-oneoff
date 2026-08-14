"""
Accelerate Blocklist Audit — Part 3: Fetch Blocklist Data

Fetches Accelerate advertiser blocklist rows pre-filtered to only
source apps + demand entities that had ≥ $1/day spend in the last 90 days.

Strategy:
  Replaces the vungle_sdk.has_vungle_sdk join (which caused a 10–20× row
  fanout) with direct entity ID filters derived from p1 and p2.

  Runs one Looker query per block level:
    global         → source apps only  (applies_to = all)
    customer       → source apps + customer IDs from p1
    advertiser_app → source apps + dest app IDs from p1
    campaign_group → source apps + campaign group IDs from p1
    campaign       → source apps + campaign IDs from p1

  Source app IDs are batched (APP_BATCH_SIZE per Looker call) to stay
  within payload limits.  Each sub-query paginates in 100K-row pages.

Resumability:
  Each block level is saved to its own checkpoint CSV as it completes.
  If a level file already exists, that level is skipped on re-run.
  This means a failed run can be resumed without re-fetching completed levels.
  Delete level checkpoint files (p3_level_*.csv) to force a full re-fetch.

Requires: p1_demand_spot.csv and p2_supply_spend.csv
Output:   output/accel_blocklist_audit/p3_blocklist.csv
"""

import os
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL      = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID     = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

OUTPUT_DIR     = "output/accel_blocklist_audit"
APP_BATCH_SIZE    = 5000    # source app IDs per Looker filter string
PAGE_SIZE         = 100_000 # rows per paginated page
MAX_ROWS_PER_BATCH= 2_000_000 # hard cap per sub-query to stop runaway fanout

os.makedirs(OUTPUT_DIR, exist_ok=True)

APPLIES_TO_MAP = {
    "all":            "global",
    "customer":       "customer",
    "app":            "advertiser_app",
    "campaign_group": "campaign_group",
    "campaign":       "campaign"
}

BLOCKLIST_FIELDS = [
    "advertiser_blocklists.blocklist_id",
    "advertiser_blocklists.exchange",
    "advertiser_blocklists.source_app_store_id",
    "advertiser_blocklists.applies_to",
    "advertiser_blocklists.blocklist_created_at_date",
    "advertiser_blocklists.blocklist_expires_at_date",
    "advertiser_blocklists.blocklist_origin",
    "advertiser_blocklists.blocklist_reason_code",
    "advertiser_blocklists.blocklist_comment",
    "advertiser_blocklists.is_auto_blocklist",
    "advertiser_blocklists.blocklist_creator_email",
    "advertiser_blocklists.campaign_id",
    "advertiser_blocklists.campaign_group_id",
    "advertiser_blocklists.app_id",
    "advertiser_blocklists.customer_id"
]

# Per-level checkpoint filenames
LEVEL_FILES = {
    "all":            f"{OUTPUT_DIR}/p3_level_global.csv",
    "customer":       f"{OUTPUT_DIR}/p3_level_customer.csv",
    "app":            f"{OUTPUT_DIR}/p3_level_advertiser_app.csv",
    "campaign_group": f"{OUTPUT_DIR}/p3_level_campaign_group.csv",
    "campaign":       f"{OUTPUT_DIR}/p3_level_campaign.csv",
}


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def get_token():
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}"
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    log("Authenticated")
    return token


def looker_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def run_page(token, filters, offset):
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model":   "blocklist",
            "view":    "advertiser_blocklists",
            "fields":  BLOCKLIST_FIELDS,
            "filters": filters,
            "sorts":   ["advertiser_blocklists.blocklist_created_at_date asc"],
            "limit":   str(PAGE_SIZE),
            "offset":  str(offset)
        },
        timeout=300
    )
    if not resp.ok:
        log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
        resp.raise_for_status()
    return resp.json()


MAX_ROWS_PER_BATCH = 500_000  # cap per sub-query to prevent vungle_sdk join fanout


def run_paginated(token, filters, label=""):
    rows   = []
    offset = 0
    while True:
        page = run_page(token, filters, offset)
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        log(f"    {label} — paginating, {offset:,} rows so far...")
        if offset >= MAX_ROWS_PER_BATCH:
            log(f"    {label} — hit {MAX_ROWS_PER_BATCH:,} row cap, stopping (fanout suspected). Dedup will clean up.", "WARN")
            break
    return rows


BATCH_SAVE_INTERVAL = 5  # save temp file within a level every N batches


def fetch_level(token, applies_to, entity_field, entity_ids_list, app_batches):
    """Fetch all blocks for one block level across all app batches.
    Saves a temp file every BATCH_SAVE_INTERVAL batches for resumability."""
    entity_filter = ",".join(str(x) for x in entity_ids_list) if entity_ids_list else None

    level_rows = []
    total_q    = len(app_batches)
    tmp_file   = f"{OUTPUT_DIR}/p3_level_{applies_to}_tmp.csv"

    for i, batch in enumerate(app_batches, 1):
        label   = f"{applies_to} batch {i}/{total_q}"
        filters = {
            "advertiser_blocklists.is_ad_format_block": "No",
            "advertiser_blocklists.exchange":            "VUNGLE,N/A",
            "advertiser_blocklists.applies_to":          applies_to,
            "advertiser_blocklists.source_app_store_id": ",".join(batch)
        }
        if entity_filter:
            filters[entity_field] = entity_filter

        rows = run_paginated(token, filters, label=label)
        level_rows.extend(rows)
        log(f"  [{i}/{total_q}] {applies_to}: {len(rows):,} rows (batch {len(batch)} apps) | level total: {len(level_rows):,}")

        if i % BATCH_SAVE_INTERVAL == 0 and level_rows:
            raw_to_df(level_rows).to_csv(tmp_file, index=False)
            log(f"  Temp save → {tmp_file}", "WARN")

    if os.path.exists(tmp_file):
        os.remove(tmp_file)

    return level_rows


def raw_to_df(rows):
    df = pd.DataFrame(rows)
    df.columns = [
        "blocklist_id", "exchange", "source_app_id", "applies_to",
        "blocklist_created_at_date", "blocklist_expires_at_date",
        "blocklist_origin", "blocklist_reason_code", "blocklist_comment",
        "is_auto_blocklist", "blocklist_creator_email",
        "campaign_id", "campaign_group_id", "app_id", "customer_id"
    ]

    df["exchange"] = df["exchange"].apply(
        lambda x: "All exchanges"
        if pd.isna(x) or str(x).strip().upper() in ["N/A", "NA", "NONE", ""]
        else x
    )

    df["block_level"] = (
        df["applies_to"].astype(str).str.strip().str.lower()
        .map(APPLIES_TO_MAP).fillna(df["applies_to"])
    )

    df["is_indefinite"] = df["blocklist_expires_at_date"].isna() | (
        df["blocklist_expires_at_date"].astype(str).str.strip().isin(["", "nan", "None"])
    )

    for col in ["source_app_id", "blocklist_id", "campaign_id",
                "campaign_group_id", "app_id", "customer_id"]:
        df[col] = df[col].astype(str).str.strip().replace("nan", "-")

    for col in ["blocklist_origin", "blocklist_reason_code", "blocklist_comment",
                "blocklist_creator_email"]:
        df[col] = df[col].fillna("-")

    return df


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    for dep in [f"{OUTPUT_DIR}/p1_demand_spot.csv",
                f"{OUTPUT_DIR}/p2_supply_spend.csv"]:
        if not os.path.exists(dep):
            raise FileNotFoundError(f"Missing required input: {dep} — run p1 and p2 first")

    final_cache = f"{OUTPUT_DIR}/p3_blocklist.csv"
    if os.path.exists(final_cache):
        log(f"Final cache found — loading {final_cache}")
        df = pd.read_csv(final_cache)
    else:
        log("═" * 55, "STEP")
        log("ACCEL BLOCKLIST AUDIT — PART 3: BLOCKLIST", "STEP")
        log("═" * 55, "STEP")

        # ── Step 1: Active source app IDs from p2 ────────────────────────
        log("Step 1: Active source app IDs from p2...", "STEP")
        p2 = pd.read_csv(f"{OUTPUT_DIR}/p2_supply_spend.csv", dtype=str)
        app_ids  = list(set(p2["market_id"].dropna().str.strip().unique()))
        batches  = [app_ids[i:i + APP_BATCH_SIZE]
                    for i in range(0, len(app_ids), APP_BATCH_SIZE)]
        log(f"  {len(app_ids):,} apps → {len(batches)} batch(es) of {APP_BATCH_SIZE}")

        # ── Step 2: Active entity IDs from p1 ────────────────────────────
        log("Step 2: Active entity IDs from p1...", "STEP")
        p1 = pd.read_csv(f"{OUTPUT_DIR}/p1_demand_spot.csv", dtype=str)

        def clean_ids(col):
            return set(
                p1[col].dropna().str.strip()
                .replace("nan", pd.NA).dropna().unique()
            )

        entity_ids = {
            "customer":       clean_ids("customer_id"),
            "dest_app":       clean_ids("dest_app_id"),
            "campaign_group": clean_ids("campaign_group_id"),
            "campaign":       clean_ids("campaign_id"),
        }
        for k, v in entity_ids.items():
            log(f"  {k}: {len(v):,} active IDs")

        # ── Step 3: Fetch per block level with checkpoint saves ───────────
        log("Step 3: Fetching per block level (checkpoints saved per level)...", "STEP")

        # (applies_to_looker_val, entity_ids_key, looker_entity_filter_field)
        block_levels = [
            ("all",            None,            None),
            ("customer",       "customer",      "advertiser_blocklists.customer_id"),
            ("app",            "dest_app",      "advertiser_blocklists.app_id"),
            ("campaign_group", "campaign_group","advertiser_blocklists.campaign_group_id"),
            ("campaign",       "campaign",      "advertiser_blocklists.campaign_id"),
        ]

        # Lazy-initialise token only when we actually need to fetch
        token = None
        level_dfs = []

        for applies_to, entity_key, entity_field in block_levels:
            level_file = LEVEL_FILES[applies_to]

            if os.path.exists(level_file):
                log(f"  Checkpoint found for '{applies_to}' — loading {level_file}")
                level_dfs.append(pd.read_csv(level_file, dtype=str))
                continue

            ids = list(entity_ids.get(entity_key, []))

            if applies_to != "all" and not ids:
                log(f"  Skipping '{applies_to}' — no matching entity IDs in p1", "WARN")
                pd.DataFrame().to_csv(level_file, index=False)
                continue

            if token is None:
                token = get_token()

            log(f"  Fetching '{applies_to}' blocks ({len(ids)} entity IDs)...", "STEP")
            raw   = fetch_level(token, applies_to, entity_field, ids, batches)
            log(f"  '{applies_to}' raw rows: {len(raw):,}")

            if raw:
                ldf = raw_to_df(raw)
            else:
                ldf = pd.DataFrame()

            ldf.to_csv(level_file, index=False)
            log(f"  Checkpoint saved → {level_file}  ({len(ldf):,} rows)")
            level_dfs.append(ldf)

        # ── Merge all levels and dedup ────────────────────────────────────
        log("Merging all level checkpoints...", "STEP")
        all_dfs = [d for d in level_dfs if len(d) > 0]
        df = pd.concat(all_dfs, ignore_index=True)

        before = len(df)
        df = df.drop_duplicates(subset=["blocklist_id", "source_app_id"]).copy()
        if before != len(df):
            log(f"Deduplicated {before - len(df):,} rows (fanout artefacts)", "WARN")

        df.to_csv(final_cache, index=False)
        log(f"Saved → {final_cache}")

    log("═" * 55, "STEP")
    log(f"Total blocks:        {len(df):,}")
    for label in ["global", "customer", "advertiser_app", "campaign_group", "campaign"]:
        ct = (df.get("block_level", pd.Series()) == label).sum()
        log(f"  {label}: {ct:,}")
    log(f"Indefinite blocks:   {df['is_indefinite'].sum() if 'is_indefinite' in df.columns else 'n/a':,}")
    log(f"Unique source apps:  {df['source_app_id'].nunique():,}")
    log("Next: run p4_build_audit.py", "STEP")
    log("═" * 55, "STEP")
