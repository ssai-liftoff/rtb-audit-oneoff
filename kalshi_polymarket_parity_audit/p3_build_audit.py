"""
Kalshi / Polymarket Parity Audit — Part 3: Build Final Audit

For each gap app, checks whether the missing advertiser is blocked on:
  1. Supply side — adomain + advertiser market ID at account and app level
  2. Demand side — Accelerate blocklist at global / customer / app / campaign level

Inputs:
  kalshi_polymarket_parity_audit/input_kalshi_gap.csv      → checks Kalshi blocks
  kalshi_polymarket_parity_audit/input_polymarket_gap.csv  → checks Polymarket blocks
  output/kalshi_polymarket_parity_audit/p1_supply_blocks.csv
  output/kalshi_polymarket_parity_audit/p2_demand_blocks.csv

Outputs:
  output/kalshi_polymarket_parity_audit/p3_kalshi_gap_audit.csv
  output/kalshi_polymarket_parity_audit/p3_polymarket_gap_audit.csv
  output/kalshi_polymarket_parity_audit/p3_combined_gap_audit.csv
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from advertiser_config import ADVERTISERS, DOMAIN_ALIASES, normalize_id
from looker_utils import log

OUTPUT_DIR = "output/kalshi_polymarket_parity_audit"

AUDIT_SPECS = [
    {
        "input": "kalshi_polymarket_parity_audit/input_kalshi_gap.csv",
        "advertiser_key": "kalshi",
        "output": f"{OUTPUT_DIR}/p3_kalshi_gap_audit.csv",
        "gap_label": "kalshi_gap",
    },
    {
        "input": "kalshi_polymarket_parity_audit/input_polymarket_gap.csv",
        "advertiser_key": "polymarket",
        "output": f"{OUTPUT_DIR}/p3_polymarket_gap_audit.csv",
        "gap_label": "polymarket_gap",
    },
]

LEVEL_LABELS = {
    "global": "Global",
    "customer": "Customer",
    "advertiser_app": "App",
    "campaign_group": "Campaign Group",
    "campaign": "Campaign",
}


def parse_blocked_list(val):
    if pd.isna(val) or str(val).strip() in ("", "nan", "None", "-", "NULL"):
        return set()
    parts = str(val).replace(";", ",").split(",")
    return {p.strip().lower() for p in parts if p.strip()}


def normalize_input(df):
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]
    rename = {}
    for col in df.columns:
        key = col.lower().replace(" ", "_")
        if key == "account_id" or ("account" in key and "id" in key and "app" not in key):
            rename[col] = "Account ID"
        elif key == "account_name" or ("account" in key and "name" in key):
            rename[col] = "Account Name"
        elif "publisher" in key and "app" in key and "id" in key:
            rename[col] = "Publisher App ID"
        elif "publisher" in key and "app" in key and "name" in key:
            rename[col] = "Publisher App Name"
        elif "market" in key:
            rename[col] = "Market ID"
    return df.rename(columns=rename)


def list_contains_any(blocked_set, values):
    normalized = {str(v).strip().lower() for v in values if str(v).strip()}
    return bool(blocked_set & normalized)


def check_supply_blocks(row, advertiser_key):
    cfg = ADVERTISERS[advertiser_key]
    domain_aliases = DOMAIN_ALIASES[cfg["domain"]]

    acct_domains = parse_blocked_list(row.get("account_blocked_ad_domains"))
    app_domains = parse_blocked_list(row.get("app_blocked_ad_domains"))
    acct_markets = parse_blocked_list(row.get("account_blocked_adv_market_ids"))
    app_markets = parse_blocked_list(row.get("app_blocked_adv_market_ids"))

    def as_flag(val):
        return "true" if val else "false"

    return {
        "Adomain Blocked (Account)": as_flag(list_contains_any(acct_domains, domain_aliases)),
        "Adomain Blocked (App)": as_flag(list_contains_any(app_domains, domain_aliases)),
        "Adv Market ID Blocked (Account)": as_flag(list_contains_any(acct_markets, cfg["market_ids"])),
        "Adv Market ID Blocked (App)": as_flag(list_contains_any(app_markets, cfg["market_ids"])),
    }


def block_matches_advertiser(block_row, advertiser_key):
    cfg = ADVERTISERS[advertiser_key]
    level = str(block_row.get("block_level", "")).strip().lower()

    if level == "global":
        return True
    if level == "customer":
        return normalize_id(block_row.get("customer_id")) in cfg["customer_ids"]
    if level == "advertiser_app":
        return normalize_id(block_row.get("app_id")) in cfg["advertiser_app_ids"]
    if level == "campaign":
        return normalize_id(block_row.get("campaign_id")) in cfg["campaign_ids"]
    return False


def summarize_demand_blocks(blocks_df, market_id, advertiser_key):
    empty = (0, "", "", "")
    if blocks_df is None or blocks_df.empty:
        return empty

    market_key = normalize_id(market_id)
    subset = blocks_df[
        (blocks_df["source_app_id"].apply(normalize_id) == market_key)
        & blocks_df.apply(lambda r: block_matches_advertiser(r, advertiser_key), axis=1)
    ]
    if subset.empty:
        return empty

    split_parts = []
    id_parts = []
    comment_parts = []

    for level, label in LEVEL_LABELS.items():
        level_rows = subset[subset["block_level"] == level]
        if level_rows.empty:
            continue

        unique_rows = level_rows.drop_duplicates(subset=["blocklist_id"], keep="first")
        ids = [
            normalize_id(blocklist_id)
            for blocklist_id in unique_rows["blocklist_id"]
            if normalize_id(blocklist_id)
        ]
        if not ids:
            continue

        split_parts.append(f"{label} ({len(ids)})")
        id_parts.append(f"{label} ({', '.join(ids)})")

        comments = []
        seen_comments = set()
        for _, row in unique_rows.iterrows():
            comment = str(row.get("blocklist_comment", "")).strip()
            if comment in ("", "-", "nan", "None") or comment in seen_comments:
                continue
            seen_comments.add(comment)
            comments.append(comment)
        if comments:
            comment_parts.append(f"{label} ({'; '.join(comments)})")

    count = subset["blocklist_id"].nunique()
    return count, ", ".join(split_parts), ", ".join(id_parts), ", ".join(comment_parts)


def build_audit(input_path, advertiser_key, supply_df, demand_df, output_path, gap_label):
    gap_df = normalize_input(pd.read_csv(input_path, dtype=str))
    for col in gap_df.columns:
        gap_df[col] = gap_df[col].astype(str).str.strip()

    supply_df = supply_df.copy()
    for col in supply_df.columns:
        supply_df[col] = supply_df[col].astype(str).str.strip()
    supply_lookup = supply_df.set_index("publisher_app_id", drop=False)

    rows = []
    for _, gap_row in gap_df.iterrows():
        app_id = gap_row.get("Publisher App ID", "")
        out = gap_row.to_dict()

        supply = supply_lookup.get(app_id)
        if supply is None:
            market_id = gap_row.get("Market ID", "")
            match = supply_df[supply_df["market_id"] == market_id]
            supply = match.iloc[0].to_dict() if len(match) else {}

        if supply:
            if "Account ID" not in out or not out.get("Account ID") or out.get("Account ID") == "nan":
                out["Account ID"] = supply.get("account_id", "")
            if "Account Name" not in out or not out.get("Account Name") or out.get("Account Name") == "nan":
                out["Account Name"] = supply.get("account_name", "")
            if not out.get("Market ID") or out.get("Market ID") == "nan":
                out["Market ID"] = supply.get("market_id", "")

        supply_checks = check_supply_blocks(supply, advertiser_key) if supply else {
            "Adomain Blocked (Account)": "false",
            "Adomain Blocked (App)": "false",
            "Adv Market ID Blocked (Account)": "false",
            "Adv Market ID Blocked (App)": "false",
        }
        out.update(supply_checks)

        market_id = out.get("Market ID", "")
        block_count, block_split, block_ids, block_comments = summarize_demand_blocks(
            demand_df, market_id, advertiser_key
        )
        out["Demand Block Count"] = block_count
        out["Demand Blocks Split"] = block_split
        out["Demand Blocklist IDs"] = block_ids
        out["Demand Blocklist Comments"] = block_comments
        out["Analyzed Advertiser"] = ADVERTISERS[advertiser_key]["domain"]
        out["Gap Type"] = gap_label
        rows.append(out)

    result = pd.DataFrame(rows)

    base_cols = [c for c in gap_df.columns if c in result.columns]
    new_cols = [
        "Adomain Blocked (Account)",
        "Adomain Blocked (App)",
        "Adv Market ID Blocked (Account)",
        "Adv Market ID Blocked (App)",
        "Demand Block Count",
        "Demand Blocks Split",
        "Demand Blocklist IDs",
        "Demand Blocklist Comments",
    ]
    meta_cols = [c for c in ["Analyzed Advertiser", "Gap Type"] if c in result.columns]
    ordered = base_cols + new_cols + meta_cols
    result = result[[c for c in ordered if c in result.columns]]

    result.to_csv(output_path, index=False)
    return result


if __name__ == "__main__":
    log("═" * 60, "STEP")
    log("KALSHI/POLYMARKET PARITY — PART 3: BUILD AUDIT", "STEP")
    log("═" * 60, "STEP")

    supply_path = f"{OUTPUT_DIR}/p1_supply_blocks.csv"
    demand_path = f"{OUTPUT_DIR}/p2_demand_blocks.csv"
    for path in [supply_path, demand_path]:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing {path} — run p1 and p2 first")

    supply_df = pd.read_csv(supply_path, dtype=str)
    demand_df = pd.read_csv(demand_path, dtype=str) if os.path.getsize(demand_path) else pd.DataFrame()

    combined_results = []
    for spec in AUDIT_SPECS:
        if not os.path.exists(spec["input"]):
            raise FileNotFoundError(f"Missing input: {spec['input']}")

        log(f"Building {spec['gap_label']} audit...", "STEP")
        result = build_audit(
            spec["input"],
            spec["advertiser_key"],
            supply_df,
            demand_df,
            spec["output"],
            spec["gap_label"],
        )

        def is_true(series):
            return series.astype(str).str.lower().eq("true")

        supply_hits = (
            is_true(result["Adomain Blocked (Account)"])
            | is_true(result["Adomain Blocked (App)"])
            | is_true(result["Adv Market ID Blocked (Account)"])
            | is_true(result["Adv Market ID Blocked (App)"])
        ).sum()
        demand_hits = (pd.to_numeric(result["Demand Block Count"], errors="coerce").fillna(0) > 0).sum()
        has_any_block = (
            is_true(result["Adomain Blocked (Account)"])
            | is_true(result["Adomain Blocked (App)"])
            | is_true(result["Adv Market ID Blocked (Account)"])
            | is_true(result["Adv Market ID Blocked (App)"])
            | (pd.to_numeric(result["Demand Block Count"], errors="coerce").fillna(0) > 0)
        )
        neither = len(result) - has_any_block.sum()

        log(f"  Rows: {len(result):,}")
        log(f"  Supply-side block signal: {supply_hits:,}")
        log(f"  Demand-side block signal: {demand_hits:,}")
        log(f"  No block found:           {neither:,}")
        log(f"  Saved → {spec['output']}")
        combined_results.append(result)

    combined_path = f"{OUTPUT_DIR}/p3_combined_gap_audit.csv"
    pd.concat(combined_results, ignore_index=True).to_csv(combined_path, index=False)
    log(f"Combined output ({sum(len(r) for r in combined_results):,} rows) → {combined_path}", "STEP")

    log("═" * 60, "STEP")
    log("PART 3 COMPLETE", "STEP")
    log("═" * 60, "STEP")
