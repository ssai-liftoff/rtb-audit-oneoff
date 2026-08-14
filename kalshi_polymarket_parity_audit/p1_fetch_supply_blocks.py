"""
Kalshi / Polymarket Parity Audit — Part 1: Fetch Supply-Side Blocks

Fetches publisher account + app blocklists from vx_analytics/vx_overview for
the gap app lists (filtered by publisher_apps.id).

Important: vx_overview is a spend explore. Without an event_date filter Looker
joins blocklist dimensions across all historical events and the query times out.
We scope to the same 7-day window used in the gap analysis.

Fields:
  publisher_accounts.blocked_ad_domains
  publisher_accounts.blocked_advertiser_market_ids
  publisher_apps.blocked_ad_domains
  publisher_apps.blocked_advertiser_market_ids

Output: output/kalshi_polymarket_parity_audit/p1_supply_blocks.csv
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from looker_utils import get_token, log, run_query

INPUT_FILES = [
    "kalshi_polymarket_parity_audit/input_kalshi_gap.csv",
    "kalshi_polymarket_parity_audit/input_polymarket_gap.csv",
]
OUTPUT_DIR = "output/kalshi_polymarket_parity_audit"
OUTPUT_FILE = f"{OUTPUT_DIR}/p1_supply_blocks.csv"

LOOKBACK_DAYS = 7

SUPPLY_FIELDS = [
    "publisher_accounts.id",
    "publisher_accounts.name",
    "publisher_apps.id",
    "publisher_apps.name",
    "publisher_apps.market_id",
    "publisher_accounts.blocked_ad_domains",
    "publisher_accounts.blocked_advertiser_market_ids",
    "publisher_apps.blocked_ad_domains",
    "publisher_apps.blocked_advertiser_market_ids",
]

os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_gap_apps():
    frames = []
    for path in INPUT_FILES:
        df = pd.read_csv(path, dtype=str)
        df.columns = [c.strip() for c in df.columns]
        rename = {}
        for col in df.columns:
            key = col.lower().replace(" ", "_")
            if "publisher" in key and "app" in key and "id" in key:
                rename[col] = "publisher_app_id"
            elif "publisher" in key and "app" in key and "name" in key:
                rename[col] = "publisher_app_name"
            elif "market" in key:
                rename[col] = "market_id"
        df = df.rename(columns=rename)
        frames.append(df[["publisher_app_id", "publisher_app_name", "market_id"]])
    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["publisher_app_id"])
    for col in out.columns:
        out[col] = out[col].astype(str).str.strip()
    return out


if __name__ == "__main__":
    log("═" * 60, "STEP")
    log("KALSHI/POLYMARKET PARITY — PART 1: SUPPLY BLOCKS", "STEP")
    log("═" * 60, "STEP")

    if os.path.exists(OUTPUT_FILE):
        log(f"Cache found — loading {OUTPUT_FILE}")
        df = pd.read_csv(OUTPUT_FILE, dtype=str)
        log(f"  {len(df):,} rows loaded")
    else:
        gap_apps = load_gap_apps()
        app_ids = gap_apps["publisher_app_id"].tolist()
        log(f"Fetching supply blocklists for {len(app_ids):,} unique gap apps...")
        log(f"  Lookback window: {LOOKBACK_DAYS} days")

        token = get_token()
        raw = run_query(
            token,
            model="vx_analytics",
            view="vx_overview",
            fields=SUPPLY_FIELDS,
            filters={
                "publisher_apps.id": ",".join(app_ids),
                "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
            },
            limit=100_000,
            label="supply blocks",
        )
        log(f"Raw rows returned: {len(raw):,}")

        df = pd.DataFrame(raw)
        df.columns = [
            "account_id",
            "account_name",
            "publisher_app_id",
            "publisher_app_name",
            "market_id",
            "account_blocked_ad_domains",
            "account_blocked_adv_market_ids",
            "app_blocked_ad_domains",
            "app_blocked_adv_market_ids",
        ]
        for col in df.columns:
            if col.endswith("_id") or col.endswith("_ids") or col.endswith("_name"):
                df[col] = df[col].astype(str).str.strip()

        df = df.drop_duplicates(subset=["publisher_app_id"], keep="first")
        missing = set(app_ids) - set(df["publisher_app_id"])
        if missing:
            log(f"  {len(missing)} apps not found in Looker", "WARN")

        df.to_csv(OUTPUT_FILE, index=False)
        log(f"Saved → {OUTPUT_FILE}")

    log("═" * 60, "STEP")
    log(f"Apps with supply block data: {len(df):,}")
    log("Next: run p2_fetch_demand_blocks.py", "STEP")
    log("═" * 60, "STEP")
