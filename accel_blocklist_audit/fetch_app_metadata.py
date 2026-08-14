"""
Fetch App Metadata — AM Region + Managed Flag

Reads 7,749 source app market IDs from data/source_apps_filter.csv and
fetches two fields from Looker for each app:
  - salesforce_accounts_monetize.am_user_region
  - publisher_accounts.is_managed

App IDs are batched in groups of 5,000 to stay within Looker payload limits.

Output: data/source_apps_metadata.csv
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

INPUT_FILE  = "output/accel_blocklist_audit/source_apps_filter.csv"
OUTPUT_FILE = "output/accel_blocklist_audit/source_apps_metadata.csv"
BATCH_SIZE  = 5000


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
    log("Authenticated")
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def fetch_batch(token, market_ids):
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=auth_headers(token),
        json={
            "model": "dmx_reports",
            "view":  "publisher_report",
            "fields": [
                "publisher_apps.market_id",
                "salesforce_accounts_monetize.am_user_region",
                "publisher_accounts.is_managed"
            ],
            "filters": {
                "publisher_apps.market_id": ",".join(market_ids)
            },
            "limit": str(len(market_ids) + 100)
        },
        timeout=300
    )
    if not resp.ok:
        log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
        resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    log("═" * 55, "STEP")
    log("FETCH APP METADATA — REGION + MANAGED FLAG", "STEP")
    log("═" * 55, "STEP")

    apps_df = pd.read_csv(INPUT_FILE, dtype=str)
    app_ids = apps_df["market_id"].dropna().str.strip().unique().tolist()
    log(f"Loaded {len(app_ids):,} app IDs from {INPUT_FILE}")

    batches = [app_ids[i:i + BATCH_SIZE] for i in range(0, len(app_ids), BATCH_SIZE)]
    log(f"Fetching in {len(batches)} batch(es) of up to {BATCH_SIZE}...")

    token    = get_token()
    all_rows = []

    for i, batch in enumerate(batches, 1):
        log(f"  Batch {i}/{len(batches)} ({len(batch)} apps)...")
        rows = fetch_batch(token, batch)
        all_rows.extend(rows)
        log(f"  → {len(rows):,} rows returned")

    log(f"Total raw rows: {len(all_rows):,}")

    df = pd.DataFrame(all_rows)
    df.columns = ["market_id", "am_user_region", "is_managed"]

    df["market_id"]     = df["market_id"].astype(str).str.strip()
    df["am_user_region"]= df["am_user_region"].fillna("-")
    df["is_managed"]    = df["is_managed"].fillna("-")

    # Deduplicate — keep one row per market_id
    before = len(df)
    df = df.drop_duplicates(subset=["market_id"], keep="first")
    if before != len(df):
        log(f"Deduplicated {before - len(df):,} duplicate rows", "WARN")

    df.to_csv(OUTPUT_FILE, index=False)

    log("═" * 55, "STEP")
    log(f"Saved → {OUTPUT_FILE}")
    log(f"Total apps:          {len(df):,}")
    log(f"Unique regions:      {df['am_user_region'].nunique():,}")
    log(f"Region breakdown:")
    for region, count in df["am_user_region"].value_counts().items():
        log(f"    {region}: {count:,}")
    log(f"Managed=Yes:         {(df['is_managed'] == 'Yes').sum():,}")
    log(f"Managed=No:          {(df['is_managed'] == 'No').sum():,}")
    log("═" * 55, "STEP")
