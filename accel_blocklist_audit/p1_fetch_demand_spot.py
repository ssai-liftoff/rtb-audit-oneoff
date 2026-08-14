"""
Accelerate Blocklist Audit — Part 1: Fetch Demand Side Spot Metrics

Fetches per-campaign VX spend for the last 30 days, aggregated at campaign
level (not source_app × campaign). This keeps the dataset small — one row
per active campaign rather than one row per source_app × campaign combination
(which would be millions of rows).

Why source_app_id is NOT in this query:
  - Source app IDs for p3 pre-filtering come from p2 (supply side).
  - p4 only needs per-entity spend (customer / dest_app / campaign_group /
    campaign), not per source_app breakdown.
  Removing source_app_id collapses the dataset from millions of rows to
  ~thousands (one per active campaign).

Revenue field: revenue_summary.revenue  (total VX ad spend in the window)
Daily revenue: revenue_30d / 30  stored as daily_revenue per row.

Source:  accelerate_analytics / accelerate_spot
Filters: exchange = VUNGLE, last 30 days, revenue >= $30 per campaign
Output:  output/accel_blocklist_audit/p1_demand_spot.csv
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

OUTPUT_DIR = "output/accel_blocklist_audit"
LOOKBACK   = 90      # days
MIN_SPEND  = 90      # minimum $ over the lookback window ($1/day average)
PAGE_SIZE  = 100_000
SAVE_EVERY = 500_000  # write temp CSV every N accumulated rows

os.makedirs(OUTPUT_DIR, exist_ok=True)


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
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def fetch_page(token, offset, retries=2):
    for attempt in range(1, retries + 2):
        try:
            resp = requests.post(
                f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
                headers=auth_headers(token),
                json={
                    "model":  "accelerate_analytics",
                    "view":   "accelerate_spot",
                    "fields": [
                        "revenue_summary.campaign_id",
                        "revenue_summary.dest_app_id",
                        "revenue_summary.dest_app_name",
                        "revenue_summary.customer_id",
                        "revenue_summary.customer_name",
                        "pinpoint__campaigns.campaign_group_id",
                        "pinpoint__campaigns.campaign_group_name",
                        "pinpoint__campaigns.display_name",
                        "revenue_summary.revenue"
                    ],
                    "filters": {
                        "revenue_summary.event_date": f"{LOOKBACK} days ago for {LOOKBACK} days",
                        "revenue_summary.exchange":   "VUNGLE",
                        "revenue_summary.revenue":    f">={MIN_SPEND}"
                    },
                    "sorts":  ["revenue_summary.revenue desc"],
                    "limit":  str(PAGE_SIZE),
                    "offset": str(offset)
                },
                timeout=600
            )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ReadTimeout:
            if attempt <= retries:
                log(f"  Timeout on attempt {attempt}, retrying...", "WARN")
            else:
                raise


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    cache = f"{OUTPUT_DIR}/p1_demand_spot.csv"
    if os.path.exists(cache):
        log(f"Cache found — loading {cache}")
        df = pd.read_csv(cache)
        log(f"  Loaded {len(df):,} rows")
    else:
        log("═" * 55, "STEP")
        log("ACCEL BLOCKLIST AUDIT — PART 1: DEMAND SPOT", "STEP")
        log("═" * 55, "STEP")
        log(f"Fetching VX spend combos (last {LOOKBACK}d, ≥${MIN_SPEND} per combo)...", "STEP")

        token    = get_token()
        log("Authenticated")
        all_rows = []
        offset   = 0
        page_num = 0

        while True:
            page_num += 1
            log(f"  Page {page_num} (offset {offset:,})...")
            page = fetch_page(token, offset)
            all_rows.extend(page)

            last_spend = page[-1].get("revenue_summary.revenue", "?") if page else "?"
            log(f"  → {len(page):,} rows | total: {len(all_rows):,} | last row 30d spend: ${last_spend}")

            # Temp save so data isn't lost on failure
            if len(all_rows) >= SAVE_EVERY and len(all_rows) % SAVE_EVERY < PAGE_SIZE:
                tmp = f"{OUTPUT_DIR}/p1_demand_spot_tmp.csv"
                pd.DataFrame(all_rows).to_csv(tmp, index=False)
                log(f"  Temp save → {tmp}", "WARN")

            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

        log(f"Total raw rows fetched: {len(all_rows):,}")

        df = pd.DataFrame(all_rows)
        df.columns = [
            "campaign_id", "dest_app_id", "dest_app_name",
            "customer_id", "customer_name",
            "campaign_group_id", "campaign_group_name", "campaign_name",
            "revenue_30d"
        ]

        df["revenue_30d"]   = pd.to_numeric(df["revenue_30d"], errors="coerce").fillna(0)
        df["daily_revenue"] = (df["revenue_30d"] / LOOKBACK).round(4)

        for col in ["campaign_id", "dest_app_id", "customer_id", "campaign_group_id"]:
            df[col] = df[col].astype(str).str.strip()

        df = df[
            df["campaign_id"].notna() &
            (df["campaign_id"] != "") &
            (df["campaign_id"] != "nan")
        ].copy()

        df.to_csv(cache, index=False)
        # Clean up temp file if it exists
        tmp = f"{OUTPUT_DIR}/p1_demand_spot_tmp.csv"
        if os.path.exists(tmp):
            os.remove(tmp)

        log(f"Saved → {cache}")

    log("═" * 55, "STEP")
    log(f"Total rows:          {len(df):,}")
    log(f"Unique campaigns:    {df['campaign_id'].nunique():,}")
    log(f"Unique customers:    {df['customer_id'].nunique():,}")
    log(f"Unique dest apps:    {df['dest_app_id'].nunique():,}")
    log(f"Unique camp groups:  {df['campaign_group_id'].nunique():,}")
    log(f"Min daily revenue:   ${df['daily_revenue'].min():,.4f}")
    log(f"Max daily revenue:   ${df['daily_revenue'].max():,.2f}")
    log("Next: run p2_fetch_supply_spend.py", "STEP")
    log("═" * 55, "STEP")
