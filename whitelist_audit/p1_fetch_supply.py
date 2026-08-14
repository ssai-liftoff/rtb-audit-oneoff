"""
Whitelist Audit — Part 1: Fetch Publisher Apps with Spend

Pulls supply-side spend from dmx_reports/publisher_report for the last 7
complete days. Returns one row per account×app combination.

Qualifying threshold: ≥$1,000/day average (≥$7,000 total over 7 days)

Output: output/whitelist_audit/p1_supply_apps.csv
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

OUTPUT_DIR      = "output/whitelist_audit"
LOOKBACK_DAYS   = 7
DAILY_THRESHOLD = 1_000
TOTAL_THRESHOLD = DAILY_THRESHOLD * LOOKBACK_DAYS
PAGE_SIZE       = 50_000

os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def get_token():
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}",
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def run_query(token, body, offset=0):
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=auth_headers(token),
        json={**body, "limit": str(PAGE_SIZE), "offset": str(offset)},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    cache = f"{OUTPUT_DIR}/p1_supply_apps.csv"

    if os.path.exists(cache):
        log(f"Cache found — loading {cache}")
        apps_df = pd.read_csv(cache)
        log(f"  {len(apps_df):,} qualifying apps loaded")
    else:
        log("═" * 60, "STEP")
        log("WHITELIST AUDIT — PART 1: FETCH PUBLISHER SUPPLY", "STEP")
        log("═" * 60, "STEP")

        token = get_token()
        log("Authenticated with Looker")

        query_body = {
            "model": "dmx_reports",
            "view":  "publisher_report",
            "fields": [
                "publisher_accounts.id",
                "publisher_accounts.name",
                "publisher_apps.id",
                "publisher_apps.name",
                "publisher_report.unified_ad_spend",
            ],
            "filters": {
                "publisher_report.event_date":       f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
                "publisher_report.unified_ad_spend": f">={TOTAL_THRESHOLD}",
            },
            "sorts": ["publisher_report.unified_ad_spend desc"],
        }

        log(f"Fetching publisher spend (last {LOOKBACK_DAYS} days, ≥${DAILY_THRESHOLD:,}/day)...")
        all_rows = []
        offset   = 0
        page_num = 0

        while True:
            page_num += 1
            log(f"  Page {page_num} (offset {offset:,})...")
            page = run_query(token, query_body, offset)
            all_rows.extend(page)
            log(f"  → {len(page):,} rows | total: {len(all_rows):,}")
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

        raw = pd.DataFrame(all_rows)
        raw.columns = ["account_id", "account_name", "app_id", "app_name", "total_7d_spend"]
        raw["total_7d_spend"] = pd.to_numeric(raw["total_7d_spend"], errors="coerce").fillna(0)

        raw = (
            raw.groupby(["account_id", "account_name", "app_id", "app_name"], dropna=False)
            .agg(total_7d_spend=("total_7d_spend", "sum"))
            .reset_index()
        )
        raw["daily_spend"] = (raw["total_7d_spend"] / LOOKBACK_DAYS).round(2)
        apps_df = raw[raw["daily_spend"] >= DAILY_THRESHOLD].copy()
        apps_df = apps_df.sort_values("daily_spend", ascending=False).reset_index(drop=True)

        apps_df.to_csv(cache, index=False)
        log(f"Saved → {cache}")

    log("═" * 60, "STEP")
    log(f"Qualifying apps (≥${DAILY_THRESHOLD:,}/day): {len(apps_df):,}")
    log(f"Unique accounts:                            {apps_df['account_id'].nunique():,}")
    log(f"Total 7d spend:                            ${apps_df['total_7d_spend'].sum():,.0f}")
    log("Next: run p2_fetch_whitelists.py", "STEP")
    log("═" * 60, "STEP")
