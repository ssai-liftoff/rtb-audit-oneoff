"""
Accelerate Blocklist Audit — Part 2: Fetch Supply Side App Spend

Fetches total ad spend per source app across all demand sources (not just
Accelerate) for the last 30 days.

Why total spend (not Accelerate-only):
  Using total supply-side spend as the denominator for app_supply_share
  gives a truer picture of the app's overall weight on the exchange.

Filtering:
  Apps must have ≥ $30 total spend in 30 days (= $1/day average).
  Applied as a Looker measure filter (HAVING equivalent) so Looker only
  returns qualifying apps, keeping response size small.
  A pandas groupby after fetch handles any residual multi-row per market_id
  (the publisher_report can return multiple rows per app due to internal joins).

Source:  dmx_reports / publisher_report
Output:  output/accel_blocklist_audit/p2_supply_spend.csv
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
LOOKBACK   = 90       # days
MIN_SPEND  = 90       # minimum $ over the lookback window ($1/day average)
PAGE_SIZE  = 100_000
SAVE_EVERY = 500_000

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


def fetch_page(token, offset):
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=auth_headers(token),
        json={
            "model":  "dmx_reports",
            "view":   "publisher_report",
            "fields": [
                "publisher_apps.market_id",
                "publisher_apps.name",
                "publisher_report.unified_ad_spend"
            ],
            "filters": {
                "publisher_report.event_date":       f"{LOOKBACK} days ago for {LOOKBACK} days",
                "publisher_report.unified_ad_spend": f">={MIN_SPEND}"
            },
            "sorts":  ["publisher_report.unified_ad_spend desc"],
            "limit":  str(PAGE_SIZE),
            "offset": str(offset)
        },
        timeout=600
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    cache = f"{OUTPUT_DIR}/p2_supply_spend.csv"
    if os.path.exists(cache):
        log(f"Cache found — loading {cache}")
        df = pd.read_csv(cache)
        log(f"  Loaded {len(df):,} rows")
    else:
        log("═" * 55, "STEP")
        log("ACCEL BLOCKLIST AUDIT — PART 2: SUPPLY SPEND", "STEP")
        log("═" * 55, "STEP")
        log(f"Fetching app spend from publisher_report (last {LOOKBACK}d, ≥${MIN_SPEND})...", "STEP")

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

            last_spend = page[-1].get("publisher_report.unified_ad_spend", "?") if page else "?"
            log(f"  → {len(page):,} rows | total: {len(all_rows):,} | last row 30d spend: ${last_spend}")

            if len(all_rows) >= SAVE_EVERY and len(all_rows) % SAVE_EVERY < PAGE_SIZE:
                tmp = f"{OUTPUT_DIR}/p2_supply_spend_tmp.csv"
                pd.DataFrame(all_rows).to_csv(tmp, index=False)
                log(f"  Temp save → {tmp}", "WARN")

            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

        log(f"Total raw rows fetched: {len(all_rows):,}")

        raw_df = pd.DataFrame(all_rows)
        raw_df.columns = ["market_id", "app_name", "spend_30d"]

        raw_df["spend_30d"]  = pd.to_numeric(raw_df["spend_30d"], errors="coerce").fillna(0)
        raw_df["market_id"]  = raw_df["market_id"].astype(str).str.strip()
        raw_df = raw_df[
            raw_df["market_id"].notna() &
            (raw_df["market_id"] != "") &
            (raw_df["market_id"] != "nan")
        ].copy()

        # Aggregate to one row per app — handles any multi-row duplicates
        df = (
            raw_df.groupby("market_id")
            .agg(
                app_name =("app_name",  "first"),
                spend_30d=("spend_30d", "sum")
            )
            .reset_index()
        )

        # Apply minimum spend threshold on the aggregated value
        before = len(df)
        df = df[df["spend_30d"] >= MIN_SPEND].copy()
        if before != len(df):
            log(f"Dropped {before - len(df):,} apps below ${MIN_SPEND} after aggregation")

        df["total_daily_spend"] = (df["spend_30d"] / LOOKBACK).round(4)
        df = df.drop(columns=["spend_30d"])

        df.to_csv(cache, index=False)
        tmp = f"{OUTPUT_DIR}/p2_supply_spend_tmp.csv"
        if os.path.exists(tmp):
            os.remove(tmp)
        log(f"Saved → {cache}")

    log("═" * 55, "STEP")
    log(f"Unique apps:         {len(df):,}")
    log(f"Total daily spend:   ${df['total_daily_spend'].sum():,.0f}")
    log(f"Min daily spend:     ${df['total_daily_spend'].min():,.4f}")
    log(f"Max daily spend:     ${df['total_daily_spend'].max():,.2f}")
    log("Next: run p3_fetch_blocklist.py", "STEP")
    log("═" * 55, "STEP")
