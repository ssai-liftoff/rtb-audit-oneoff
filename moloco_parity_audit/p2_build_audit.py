"""
Moloco RTB Parity Audit — Part 2: Build Audit

Supply-side only (no Accelerate demand blocks):
  - Adomain blocked (account / app)
  - Adv market ID blocked (account / app)
  - IAB category blocked — IAB13 for Kalshi gap, IAB12 for Polymarket gap

Outputs:
  output/moloco_parity_audit/p2_kalshi_gap_audit.csv
  output/moloco_parity_audit/p2_polymarket_gap_audit.csv
  output/moloco_parity_audit/p2_combined_gap_audit.csv
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))
from advertiser_config import ADVERTISERS, DOMAIN_ALIASES

OUTPUT_DIR = "output/moloco_parity_audit"

AUDIT_SPECS = [
    {
        "input": "moloco_parity_audit/input_kalshi_gap.csv",
        "advertiser_key": "kalshi",
        "output": f"{OUTPUT_DIR}/p2_kalshi_gap_audit.csv",
        "gap_label": "kalshi_gap",
    },
    {
        "input": "moloco_parity_audit/input_polymarket_gap.csv",
        "advertiser_key": "polymarket",
        "output": f"{OUTPUT_DIR}/p2_polymarket_gap_audit.csv",
        "gap_label": "polymarket_gap",
    },
]


def log(msg, level="INFO"):
    from datetime import datetime

    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def parse_blocked_list(val):
    if pd.isna(val) or str(val).strip() in ("", "nan", "None", "-", "NULL"):
        return set()
    parts = str(val).replace(";", ",").split(",")
    return {p.strip().upper() for p in parts if p.strip()}


def as_flag(val):
    return "true" if val else "false"


def list_contains_any(blocked_set, values):
    normalized = {str(v).strip().lower() for v in values if str(v).strip()}
    blocked_lower = {v.lower() for v in blocked_set}
    return bool(blocked_lower & normalized)


def iab_blocked(blocked_set, iab_code):
    code = iab_code.strip().upper()
    return code in blocked_set or any(item.startswith(code) for item in blocked_set)


def check_supply_blocks(row, advertiser_key):
    cfg = ADVERTISERS[advertiser_key]
    domain_aliases = DOMAIN_ALIASES[cfg["domain"]]
    iab_code = cfg["iab_code"]
    iab_col = f"{iab_code} Blocked"

    acct_domains = parse_blocked_list(row.get("account_blocked_ad_domains"))
    app_domains = parse_blocked_list(row.get("app_blocked_ad_domains"))
    acct_markets = parse_blocked_list(row.get("account_blocked_adv_market_ids"))
    app_markets = parse_blocked_list(row.get("app_blocked_adv_market_ids"))
    acct_iab = parse_blocked_list(row.get("account_blocked_iab_categories"))
    app_iab = parse_blocked_list(row.get("app_blocked_iab_categories"))

    return {
        "Adomain Blocked (Account)": as_flag(list_contains_any(acct_domains, domain_aliases)),
        "Adomain Blocked (App)": as_flag(list_contains_any(app_domains, domain_aliases)),
        "Adv Market ID Blocked (Account)": as_flag(list_contains_any(acct_markets, cfg["market_ids"])),
        "Adv Market ID Blocked (App)": as_flag(list_contains_any(app_markets, cfg["market_ids"])),
        iab_col: as_flag(iab_blocked(acct_iab, iab_code) or iab_blocked(app_iab, iab_code)),
    }


def build_audit(input_path, advertiser_key, supply_df, output_path, gap_label):
    gap_df = pd.read_csv(input_path, dtype=str)
    gap_df.columns = [c.strip() for c in gap_df.columns]
    for col in gap_df.columns:
        gap_df[col] = gap_df[col].astype(str).str.strip()

    app_col = next(
        c for c in gap_df.columns if "publisher" in c.lower() and "app" in c.lower() and "id" in c.lower()
    )

    supply_df = supply_df.copy()
    for col in supply_df.columns:
        supply_df[col] = supply_df[col].astype(str).str.strip()
    supply_lookup = supply_df.set_index("publisher_app_id", drop=False)

    iab_col = f"{ADVERTISERS[advertiser_key]['iab_code']} Blocked"
    rows = []
    for _, gap_row in gap_df.iterrows():
        out = gap_row.to_dict()
        app_id = gap_row[app_col]
        supply = supply_lookup.get(app_id)
        if supply is None:
            market_cols = [c for c in gap_df.columns if "market" in c.lower()]
            if market_cols:
                market_id = gap_row[market_cols[0]]
                match = supply_df[supply_df["market_id"] == market_id]
                supply = match.iloc[0].to_dict() if len(match) else {}
            else:
                supply = {}

        checks = check_supply_blocks(supply, advertiser_key) if supply else {
            "Adomain Blocked (Account)": "false",
            "Adomain Blocked (App)": "false",
            "Adv Market ID Blocked (Account)": "false",
            "Adv Market ID Blocked (App)": "false",
            iab_col: "false",
        }
        out.update(checks)
        out["Analyzed Advertiser"] = ADVERTISERS[advertiser_key]["domain"]
        out["Gap Type"] = gap_label
        rows.append(out)

    result = pd.DataFrame(rows)
    base_cols = list(gap_df.columns)
    check_cols = [
        "Adomain Blocked (Account)",
        "Adomain Blocked (App)",
        "Adv Market ID Blocked (Account)",
        "Adv Market ID Blocked (App)",
        iab_col,
    ]
    meta_cols = ["Analyzed Advertiser", "Gap Type"]
    result = result[[c for c in base_cols + check_cols + meta_cols if c in result.columns]]
    result.to_csv(output_path, index=False)
    return result


if __name__ == "__main__":
    log("═" * 60, "STEP")
    log("MOLOCO PARITY — PART 2: BUILD AUDIT", "STEP")
    log("═" * 60, "STEP")

    supply_path = f"{OUTPUT_DIR}/p1_supply_blocks.csv"
    if not os.path.exists(supply_path):
        raise FileNotFoundError(f"Missing {supply_path} — run p1 first")

    supply_df = pd.read_csv(supply_path, dtype=str)
    combined = []

    for spec in AUDIT_SPECS:
        log(f"Building {spec['gap_label']}...", "STEP")
        result = build_audit(
            spec["input"],
            spec["advertiser_key"],
            supply_df,
            spec["output"],
            spec["gap_label"],
        )
        combined.append(result)

        iab_col = f"{ADVERTISERS[spec['advertiser_key']]['iab_code']} Blocked"

        def is_true(s):
            return s.astype(str).str.lower().eq("true")

        any_block = (
            is_true(result["Adomain Blocked (Account)"])
            | is_true(result["Adomain Blocked (App)"])
            | is_true(result["Adv Market ID Blocked (Account)"])
            | is_true(result["Adv Market ID Blocked (App)"])
            | is_true(result[iab_col])
        )
        log(f"  Rows: {len(result):,} | Any supply block: {any_block.sum():,}")
        log(f"  Saved → {spec['output']}")

    combined_path = f"{OUTPUT_DIR}/p2_combined_gap_audit.csv"
    pd.concat(combined, ignore_index=True).to_csv(combined_path, index=False)
    log(f"Combined output ({sum(len(r) for r in combined):,} rows) → {combined_path}", "STEP")
    log("═" * 60, "STEP")
    log("COMPLETE", "STEP")
    log("═" * 60, "STEP")
