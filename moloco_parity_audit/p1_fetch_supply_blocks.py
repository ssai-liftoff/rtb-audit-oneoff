"""
Moloco RTB Parity Audit — Part 1: Fetch Supply-Side Blocks

Fetches publisher blocklists from vx_analytics/vx_overview for Moloco gap apps:
  - blocked ad domains + advertiser market IDs (account + app)
  - blocked IAB categories (account + app)

Output: output/moloco_parity_audit/p1_supply_blocks.csv
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kalshi_polymarket_parity_audit"))
from looker_utils import get_token, log, run_query

INPUT_FILES = [
    "moloco_parity_audit/input_kalshi_gap.csv",
    "moloco_parity_audit/input_polymarket_gap.csv",
]
OUTPUT_DIR = "output/moloco_parity_audit"
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
    "publisher_accounts.blocked_iab_categories",
    "publisher_apps.blocked_iab_categories",
]

os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_gap_apps():
    frames = []
    for path in INPUT_FILES:
        df = pd.read_csv(path, dtype=str)
        df.columns = [c.strip() for c in df.columns]
        app_col = next(c for c in df.columns if "publisher" in c.lower() and "app" in c.lower() and "id" in c.lower())
        frames.append(df[[app_col]].rename(columns={app_col: "publisher_app_id"}))
    out = pd.concat(frames, ignore_index=True).drop_duplicates(subset=["publisher_app_id"])
    out["publisher_app_id"] = out["publisher_app_id"].astype(str).str.strip()
    return out


if __name__ == "__main__":
    log("═" * 60, "STEP")
    log("MOLOCO PARITY — PART 1: SUPPLY BLOCKS", "STEP")
    log("═" * 60, "STEP")

    if os.path.exists(OUTPUT_FILE):
        log(f"Cache found — loading {OUTPUT_FILE}")
        df = pd.read_csv(OUTPUT_FILE, dtype=str)
        log(f"  {len(df):,} rows loaded")
    else:
        gap_apps = load_gap_apps()
        app_ids = gap_apps["publisher_app_id"].tolist()
        log(f"Fetching supply blocklists for {len(app_ids):,} gap apps...")

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
            label="moloco supply blocks",
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
            "account_blocked_iab_categories",
            "app_blocked_iab_categories",
        ]
        for col in df.columns:
            df[col] = df[col].astype(str).str.strip()

        df = df.drop_duplicates(subset=["publisher_app_id"], keep="first")
        missing = set(app_ids) - set(df["publisher_app_id"])
        if missing:
            log(f"  {len(missing)} apps not found in Looker", "WARN")

        df.to_csv(OUTPUT_FILE, index=False)
        log(f"Saved → {OUTPUT_FILE}")

    log("═" * 60, "STEP")
    log(f"Apps with supply block data: {len(df):,}")
    log("Next: run p2_build_audit.py", "STEP")
    log("═" * 60, "STEP")
