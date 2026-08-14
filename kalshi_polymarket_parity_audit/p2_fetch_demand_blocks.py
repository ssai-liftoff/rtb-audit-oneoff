"""
Kalshi / Polymarket Parity Audit — Part 2: Fetch Demand-Side (Accelerate) Blocks

Fetches Accelerate advertiser blocklists for gap source apps, filtered to:
  - exchange = VUNGLE or N/A
  - is_ad_format_block = No
  - is_live = Yes
  - source_app_store_id = gap app market IDs
  - entity IDs for Kalshi and Polymarket (customer, advertiser app, campaign)

One query per applies_to level (global, customer, app, campaign), OR'd together
when summarizing in p3.

Output: output/kalshi_polymarket_parity_audit/p2_demand_blocks.csv
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from advertiser_config import ADVERTISERS, normalize_id
from looker_utils import get_token, log, run_paginated

INPUT_FILES = [
    "kalshi_polymarket_parity_audit/input_kalshi_gap.csv",
    "kalshi_polymarket_parity_audit/input_polymarket_gap.csv",
]
OUTPUT_DIR = "output/kalshi_polymarket_parity_audit"
OUTPUT_FILE = f"{OUTPUT_DIR}/p2_demand_blocks.csv"

APPLIES_TO_MAP = {
    "all": "global",
    "customer": "customer",
    "app": "advertiser_app",
    "campaign_group": "campaign_group",
    "campaign": "campaign",
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
    "advertiser_blocklists.customer_id",
]

os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_market_ids():
    frames = []
    for path in INPUT_FILES:
        df = pd.read_csv(path, dtype=str)
        df.columns = [c.strip() for c in df.columns]
        market_col = next(c for c in df.columns if "market" in c.lower())
        frames.append(df[[market_col]].rename(columns={market_col: "market_id"}))
    out = pd.concat(frames, ignore_index=True)
    out["market_id"] = out["market_id"].astype(str).str.strip()
    return sorted(out["market_id"].drop_duplicates().tolist())


def all_entity_ids():
    customers = set()
    apps = set()
    campaigns = set()
    for cfg in ADVERTISERS.values():
        customers.update(cfg["customer_ids"])
        apps.update(cfg["advertiser_app_ids"])
        campaigns.update(cfg["campaign_ids"])
    return customers, apps, campaigns


def raw_to_df(rows):
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df.columns = [
        "blocklist_id",
        "exchange",
        "source_app_id",
        "applies_to",
        "blocklist_created_at_date",
        "blocklist_expires_at_date",
        "blocklist_origin",
        "blocklist_reason_code",
        "blocklist_comment",
        "is_auto_blocklist",
        "blocklist_creator_email",
        "campaign_id",
        "campaign_group_id",
        "app_id",
        "customer_id",
    ]

    df["exchange"] = df["exchange"].apply(
        lambda x: "All exchanges"
        if pd.isna(x) or str(x).strip().upper() in ["N/A", "NA", "NONE", ""]
        else x
    )
    df["block_level"] = (
        df["applies_to"].astype(str).str.strip().str.lower().map(APPLIES_TO_MAP).fillna(df["applies_to"])
    )
    for col in [
        "source_app_id",
        "blocklist_id",
        "campaign_id",
        "campaign_group_id",
        "app_id",
        "customer_id",
    ]:
        df[col] = df[col].apply(normalize_id).replace("", "-")

    return df


def fetch_level(token, applies_to, entity_field, entity_ids, market_ids):
    filters = {
        "advertiser_blocklists.is_ad_format_block": "No",
        "advertiser_blocklists.is_live": "Yes",
        "advertiser_blocklists.exchange": "VUNGLE,N/A",
        "advertiser_blocklists.applies_to": applies_to,
        "advertiser_blocklists.source_app_store_id": ",".join(market_ids),
    }
    if entity_field and entity_ids:
        filters[entity_field] = ",".join(sorted(entity_ids))

    label = f"{applies_to} ({len(market_ids)} source apps)"
    return run_paginated(
        token,
        model="blocklist",
        view="advertiser_blocklists",
        fields=BLOCKLIST_FIELDS,
        filters=filters,
        sorts=["advertiser_blocklists.blocklist_created_at_date asc"],
        label=label,
    )


if __name__ == "__main__":
    log("═" * 60, "STEP")
    log("KALSHI/POLYMARKET PARITY — PART 2: DEMAND BLOCKS", "STEP")
    log("═" * 60, "STEP")

    if os.path.exists(OUTPUT_FILE):
        log(f"Cache found — loading {OUTPUT_FILE}")
        df = pd.read_csv(OUTPUT_FILE, dtype=str)
        log(f"  {len(df):,} rows loaded")
    else:
        market_ids = load_market_ids()
        customers, apps, campaigns = all_entity_ids()
        log(f"Source apps (market IDs): {len(market_ids):,}")
        log(f"Entity scope — customers: {len(customers)}, apps: {len(apps)}, campaigns: {len(campaigns)}")

        token = get_token()
        level_specs = [
            ("all", None, None),
            ("customer", "advertiser_blocklists.customer_id", customers),
            ("app", "advertiser_blocklists.app_id", apps),
            ("campaign", "advertiser_blocklists.campaign_id", campaigns),
        ]

        all_rows = []
        for applies_to, entity_field, entity_ids in level_specs:
            log(f"Fetching '{applies_to}' blocks...", "STEP")
            raw = fetch_level(token, applies_to, entity_field, entity_ids, market_ids)
            log(f"  Raw rows: {len(raw):,}")
            all_rows.extend(raw)

        df = raw_to_df(all_rows)
        if len(df):
            before = len(df)
            df = df.drop_duplicates(subset=["blocklist_id", "source_app_id"]).copy()
            if before != len(df):
                log(f"Deduplicated {before - len(df):,} fanout rows", "WARN")

        df.to_csv(OUTPUT_FILE, index=False)
        log(f"Saved → {OUTPUT_FILE}")

    log("═" * 60, "STEP")
    log(f"Total demand blocks: {len(df):,}")
    if len(df):
        for level in ["global", "customer", "advertiser_app", "campaign_group", "campaign"]:
            ct = (df["block_level"] == level).sum()
            if ct:
                log(f"  {level}: {ct:,}")
    log("Next: run p3_build_audit.py", "STEP")
    log("═" * 60, "STEP")
