"""
FIFA Blocklist Audit — IAB Spend Breakdown by Adomain

For each FIFA adomain, shows spend distribution across IAB codes as percentages.
No RTB/exchange filter — reflects total spend across all buyers.

Fetches directly from vx_analytics/vx_overview (own Looker cache separate from p2).

Output: output/fifa_blocklist_audit/iab_spend_breakdown.csv
"""

import os
import re
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL      = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID     = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

OUTPUT_DIR    = "output/fifa_blocklist_audit"
ADOMAINS_FILE = "fifa_blocklist_audit/fifa_adomains.csv"
LOOKBACK_DAYS = 7
PAGE_SIZE     = 10_000
IAB_CODE_RE   = re.compile(r"^IAB\d")

# Separate cache so p2's RTB-filtered data is untouched
RAW_CACHE = f"{OUTPUT_DIR}/iab_spend_raw.csv"


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def get_token():
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}"
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    log("═" * 60, "STEP")
    log("FIFA BLOCKLIST AUDIT — IAB SPEND BREAKDOWN", "STEP")
    log("═" * 60, "STEP")

    adomains = pd.read_csv(ADOMAINS_FILE)["adomain"].str.lower().str.strip().tolist()

    if os.path.exists(RAW_CACHE):
        log(f"Cache found — loading {RAW_CACHE}")
        raw = pd.read_csv(RAW_CACHE)
    else:
        log("Fetching IAB spend from vx_overview (all exchanges, no RTB filter)...")
        token = get_token()
        resp = requests.post(
            f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
            headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
            json={
                "model":  "vx_analytics",
                "view":   "vx_overview",
                "fields": [
                    "vx_overview.adomain",
                    "vx_overview.content_category_code",
                    "vx_overview.unified_ad_spend",
                ],
                "filters": {
                    "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
                    "vx_overview.adomain":    ",".join(adomains),
                },
                "sorts":  ["vx_overview.unified_ad_spend desc"],
                "limit":  str(PAGE_SIZE),
            },
            timeout=120,
        )
        resp.raise_for_status()
        rows = resp.json()
        log(f"  → {len(rows):,} rows returned")

        raw = pd.DataFrame(rows)
        if len(raw):
            raw.columns = ["adomain", "iab_code", "spend"]
            raw["spend"]    = pd.to_numeric(raw["spend"], errors="coerce").fillna(0)
            raw["adomain"]  = raw["adomain"].str.strip().str.lower()
            raw["iab_code"] = raw["iab_code"].astype(str).str.strip()
            # Keep only valid IAB codes
            raw = raw[raw["iab_code"].apply(lambda c: bool(IAB_CODE_RE.match(c)) and len(c) <= 10)]
            raw["daily_spend"] = (raw["spend"] / LOOKBACK_DAYS).round(4)
        else:
            raw = pd.DataFrame(columns=["adomain", "iab_code", "spend", "daily_spend"])

        raw.to_csv(RAW_CACHE, index=False)
        log(f"Saved raw cache → {RAW_CACHE}")

    raw = raw[raw["daily_spend"] > 0].copy()

    # All FIFA adomains (to include zero-spend ones in output)
    all_adomains = pd.read_csv(ADOMAINS_FILE)["adomain"].str.lower().str.strip().tolist()

    rows = []
    for adomain in all_adomains:
        subset = raw[raw["adomain"] == adomain].copy()

        if subset.empty:
            rows.append({
                "Adomain":             adomain,
                "Total Daily Spend ($)": 0,
                "IAB Spend Breakdown": "no spend in window",
            })
            continue

        total = subset["daily_spend"].sum()
        subset = subset.sort_values("daily_spend", ascending=False)

        # Format: "IAB9-7 (85.2%), IAB17 (9.1%), IAB1-3 (5.7%)"
        parts = [
            f"{row['iab_code']} ({row['daily_spend'] / total * 100:.1f}%)"
            for _, row in subset.iterrows()
        ]
        breakdown = ", ".join(parts)

        rows.append({
            "Adomain":               adomain,
            "Total Daily Spend ($)": round(total, 2),
            "IAB Spend Breakdown":   breakdown,
        })

    out_df = pd.DataFrame(rows).sort_values("Total Daily Spend ($)", ascending=False).reset_index(drop=True)

    out_path = f"{OUTPUT_DIR}/iab_spend_breakdown.csv"
    out_df.to_csv(out_path, index=False)
    log(f"Saved → {out_path}")

    log("═" * 60, "STEP")
    log(f"Adomains with spend:    {(out_df['Total Daily Spend ($)'] > 0).sum():,} / {len(out_df):,}")
    log("")
    log("IAB breakdown per adomain (all exchanges):")
    for _, row in out_df.iterrows():
        log(f"  {row['Adomain']:<40} ${row['Total Daily Spend ($)']:>10,.2f}/day   {row['IAB Spend Breakdown']}")
    log("═" * 60, "STEP")
