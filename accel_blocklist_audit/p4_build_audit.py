"""
Accelerate Blocklist Audit — Part 4: Build Audit + Calculate Uplift

Uplift methodology (unified across all block levels):
──────────────────────────────────────────────────────
  app_supply_share     = source_app_total_daily_spend
                         ÷ total_supply_daily_spend   (all VX apps, all demand)

  estimated_uplift     = blocked_entity_vx_daily_spend × app_supply_share

Intuition: the source app represents X% of total supply-side spend. If the
blocked entity could reach it, it would likely allocate roughly X% of its
VX spend there.

For global blocks there is no specific entity — the blocked entity is all
Accelerate on VX, so blocked_entity_vx_daily_spend = total Accelerate
daily spend on VX across the 90-day window.

Verification columns (source_app_total_daily_spend, total_supply_daily_spend,
app_supply_share, blocked_entity_vx_daily_spend) are included for spot-checking.

Inputs:
  output/accel_blocklist_audit/p1_demand_spot.csv
  output/accel_blocklist_audit/p2_supply_spend.csv
  output/accel_blocklist_audit/p3_blocklist.csv

Output:
  output/accel_blocklist_audit/p4_accel_blocklist_audit.csv
"""

import os
import pandas as pd
from datetime import datetime

OUTPUT_DIR = "output/accel_blocklist_audit"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


if __name__ == "__main__":
    log("═" * 55, "STEP")
    log("ACCEL BLOCKLIST AUDIT — PART 4: BUILD AUDIT", "STEP")
    log("═" * 55, "STEP")

    for path in [
        f"{OUTPUT_DIR}/p1_demand_spot.csv",
        f"{OUTPUT_DIR}/p2_supply_spend.csv",
        f"{OUTPUT_DIR}/p3_blocklist.csv"
    ]:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing: {path} — run previous parts first")

    # ── Load inputs ────────────────────────────────────────────────────────────
    log("Loading data from parts 1–3...", "STEP")
    demand_df = pd.read_csv(f"{OUTPUT_DIR}/p1_demand_spot.csv", dtype=str)
    supply_df = pd.read_csv(f"{OUTPUT_DIR}/p2_supply_spend.csv", dtype=str)
    blocks_df = pd.read_csv(f"{OUTPUT_DIR}/p3_blocklist.csv",   dtype=str)

    demand_df["daily_revenue"]    = pd.to_numeric(demand_df["daily_revenue"],    errors="coerce").fillna(0)
    supply_df["total_daily_spend"]= pd.to_numeric(supply_df["total_daily_spend"],errors="coerce").fillna(0)

    log(f"  Demand rows (p1): {len(demand_df):,}")
    log(f"  Supply rows (p2): {len(supply_df):,}")
    log(f"  Block rows  (p3): {len(blocks_df):,}")

    # ── Global constants ───────────────────────────────────────────────────────
    log("Computing global constants...", "STEP")

    # Deduplicate supply on market_id before summing
    supply_deduped = (
        supply_df.sort_values("total_daily_spend", ascending=False)
        .drop_duplicates(subset=["market_id"], keep="first")
    )
    total_supply_daily_spend = supply_deduped["total_daily_spend"].sum()
    log(f"  Total supply daily spend (all demand, 90d avg): ${total_supply_daily_spend:,.0f}")

    # Total Accelerate VX daily spend — used as entity spend for global blocks
    total_accel_vx_daily_spend = demand_df["daily_revenue"].sum()
    log(f"  Total Accelerate VX daily spend (90d avg):      ${total_accel_vx_daily_spend:,.0f}")

    # ── Blocked entity VX daily spend lookups ─────────────────────────────────
    log("Building blocked entity spend lookups from p1...", "STEP")
    customer_vx_spend   = demand_df.groupby("customer_id")["daily_revenue"].sum().to_dict()
    dest_app_vx_spend   = demand_df.groupby("dest_app_id")["daily_revenue"].sum().to_dict()
    camp_group_vx_spend = demand_df.groupby("campaign_group_id")["daily_revenue"].sum().to_dict()
    campaign_vx_spend   = demand_df.groupby("campaign_id")["daily_revenue"].sum().to_dict()

    # ── Entity name lookups ────────────────────────────────────────────────────
    customer_name_lkp   = demand_df.drop_duplicates("customer_id").set_index("customer_id")["customer_name"].to_dict()
    dest_app_name_lkp   = demand_df.drop_duplicates("dest_app_id").set_index("dest_app_id")["dest_app_name"].to_dict()
    camp_group_name_lkp = demand_df.drop_duplicates("campaign_group_id").set_index("campaign_group_id")["campaign_group_name"].to_dict()
    campaign_name_lkp   = demand_df.drop_duplicates("campaign_id").set_index("campaign_id")["campaign_name"].to_dict()

    # ── Supply side lookup ─────────────────────────────────────────────────────
    supply_lookup = supply_deduped.set_index("market_id")[[
        "app_name", "total_daily_spend"
    ]].to_dict("index")

    # ── Build audit rows ───────────────────────────────────────────────────────
    log("Building audit rows...", "STEP")
    rows = []

    for _, block in blocks_df.iterrows():
        source_app_id = str(block["source_app_id"])
        block_level   = block["block_level"]

        supply       = supply_lookup.get(source_app_id, {})
        app_name     = supply.get("app_name", "-")
        total_daily  = float(supply.get("total_daily_spend", 0))

        app_supply_share = (
            total_daily / total_supply_daily_spend
            if total_supply_daily_spend > 0 else 0
        )

        if block_level == "global":
            entity_id       = "-"
            entity_name     = "-"
            entity_vx_spend = total_accel_vx_daily_spend

        elif block_level == "customer":
            entity_id       = str(block["customer_id"])
            entity_name     = customer_name_lkp.get(entity_id, "-")
            entity_vx_spend = float(customer_vx_spend.get(entity_id, 0))

        elif block_level == "advertiser_app":
            entity_id       = str(block["app_id"])
            entity_name     = dest_app_name_lkp.get(entity_id, "-")
            entity_vx_spend = float(dest_app_vx_spend.get(entity_id, 0))

        elif block_level == "campaign_group":
            entity_id       = str(block["campaign_group_id"])
            entity_name     = camp_group_name_lkp.get(entity_id, "-")
            entity_vx_spend = float(camp_group_vx_spend.get(entity_id, 0))

        elif block_level == "campaign":
            entity_id       = str(block["campaign_id"])
            entity_name     = campaign_name_lkp.get(entity_id, "-")
            entity_vx_spend = float(campaign_vx_spend.get(entity_id, 0))

        else:
            entity_id, entity_name, entity_vx_spend = "-", "-", 0.0

        estimated_uplift = round(entity_vx_spend * app_supply_share, 2)

        created_date_str  = block["blocklist_created_at_date"]
        expires_date_str  = block.get("blocklist_expires_at_date", "-")
        is_indefinite_val = str(block["is_indefinite"]).strip().lower()

        # Year and Quarter derived from created date
        try:
            created_dt = pd.to_datetime(created_date_str)
            year_and_quarter = f"{created_dt.year} Q{created_dt.quarter}"
        except Exception:
            year_and_quarter = "-"

        # Days to unblock (None / "-" when indefinite or unparseable)
        days_to_unblock = "-"
        if is_indefinite_val not in ("true", "1", "yes") and expires_date_str not in ("-", "", None):
            try:
                expires_dt      = pd.to_datetime(expires_date_str)
                today           = pd.Timestamp.today().normalize()
                days_to_unblock = max(0, (expires_dt - today).days)
            except Exception:
                days_to_unblock = "-"

        # Is definite + longer than 60 days
        if is_indefinite_val not in ("true", "1", "yes") and isinstance(days_to_unblock, int):
            is_definite_long = days_to_unblock > 60
        else:
            is_definite_long = False

        rows.append({
            "blocklist_id":                  block["blocklist_id"],
            "source_app_id":                 source_app_id,
            "source_app_name":               app_name,
            "block_level":                   block_level,
            "blocked_entity_id":             entity_id,
            "blocked_entity_name":           entity_name,
            "exchange":                      block["exchange"],
            "blocklist_created_at_date":     created_date_str,
            "year_and_quarter_created":      year_and_quarter,
            "blocklist_expires_at_date":     expires_date_str,
            "is_indefinite":                 block["is_indefinite"],
            "days_to_unblock":               days_to_unblock,
            "is_definite_longer_than_60d":   is_definite_long,
            "blocklist_origin":              block["blocklist_origin"],
            "blocklist_reason_code":         block["blocklist_reason_code"],
            "blocklist_comment":             block["blocklist_comment"],
            "is_auto_blocklist":             block["is_auto_blocklist"],
            "blocklist_creator_email":       block["blocklist_creator_email"],
            "source_app_total_daily_spend":  round(total_daily, 2),
            "app_supply_share":              round(app_supply_share, 8),
            "blocked_entity_vx_daily_spend": round(entity_vx_spend, 2),
            "estimated_daily_uplift":        estimated_uplift,
        })

    log(f"  Total rows built: {len(rows):,}")

    result_df = (
        pd.DataFrame(rows)
        .sort_values("estimated_daily_uplift", ascending=False)
        .reset_index(drop=True)
    )

    output_path = f"{OUTPUT_DIR}/p4_accel_blocklist_audit.csv"
    result_df.to_csv(output_path, index=False)

    log("═" * 55, "STEP")
    log("PART 4 COMPLETE", "STEP")
    log(f"  Total rows:               {len(result_df):,}", "STEP")
    log(f"  Unique source apps:       {result_df['source_app_id'].nunique():,}", "STEP")
    log(f"  Global blocks:            {(result_df['block_level'] == 'global').sum():,}", "STEP")
    log(f"  Non-global blocks:        {(result_df['block_level'] != 'global').sum():,}", "STEP")
    log(f"  Est. total daily uplift:  ${result_df['estimated_daily_uplift'].sum():,.0f}", "STEP")
    log(f"  Output: {output_path}", "STEP")
    log("═" * 55, "STEP")
