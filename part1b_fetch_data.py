"""
Part 1b: Fetch RTB account spend + network benchmark (top 25 countries + placement types)
Outputs:
  - output/rtb_accounts.csv
  - output/top25_countries.csv
  - output/top_placement_types.csv
"""

import os
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

LOOKBACK_DAYS = 7
RTB_SPEND_THRESHOLD = 1000  # $1000/day minimum for RTB accounts

os.makedirs("output", exist_ok=True)


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_looker_token():
    print("Authenticating with Looker API...")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}"
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    print("✓ Authenticated")
    return token


def looker_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def run_looker_query(token, model, explore, fields, filters, sorts=None, limit=5000):
    payload = {
        "model": model,
        "view": explore,
        "fields": fields,
        "filters": filters,
        "limit": str(limit)
    }
    if sorts:
        payload["sorts"] = sorts
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json=payload
    )
    resp.raise_for_status()
    return resp.json()


# ── Step 1: RTB accounts + connections with spend > $1000/day ─────────────────

def fetch_rtb_accounts(token):
    print("\nFetching RTB account + connection spend (vx_overview)...")

    data = run_looker_query(
        token,
        model="vx_analytics",
        explore="vx_overview",
        fields=[
            "rtb_accounts.id",
            "rtb_accounts.name",
            "rtb_accounts.contact_name",
            "rtb_connections.id",
            "rtb_connections.name",
            "vx_overview.unified_ad_spend"
        ],
        filters={
            "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
        },
        limit=10000
    )

    df = pd.DataFrame(data)
    df.columns = [
        "rtb_account_id", "rtb_account_name", "rtb_contact_name",
        "rtb_connection_id", "rtb_connection_name", "total_spend_7d"
    ]

    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS

    # Aggregate to account level for filtering
    account_spend = df.groupby("rtb_account_id")["daily_spend"].sum().reset_index()
    account_spend.columns = ["rtb_account_id", "account_daily_spend"]

    # Filter accounts with > $1000/day
    eligible_accounts = account_spend[account_spend["account_daily_spend"] >= RTB_SPEND_THRESHOLD]["rtb_account_id"].tolist()

    before = df["rtb_account_id"].nunique()
    df = df[df["rtb_account_id"].isin(eligible_accounts)].copy()
    print(f"✓ {df['rtb_account_id'].nunique()} RTB accounts with >${RTB_SPEND_THRESHOLD}/day (filtered from {before})")
    print(f"  Covering {len(df)} connections total")

    df.to_csv("output/rtb_accounts.csv", index=False)
    print("✓ Saved to output/rtb_accounts.csv")
    return df


# ── Step 2: Top 25 countries by publisher spend ───────────────────────────────

def fetch_top_countries(token):
    print("\nFetching top 25 countries by publisher spend...")

    data = run_looker_query(
        token,
        model="dmx_reports",
        explore="publisher_report",
        fields=[
            "geo_details.code",
            "publisher_report.unified_ad_spend"
        ],
        filters={
            "publisher_report.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
        },
        sorts=["publisher_report.unified_ad_spend desc"],
        limit=25
    )

    df = pd.DataFrame(data)
    df.columns = ["geo_code", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS

    # Calculate % of total network spend
    total = df["total_spend_7d"].sum()
    df["pct_of_network"] = (df["total_spend_7d"] / total * 100).round(2)
    df["rank"] = range(1, len(df) + 1)

    # Normalise geo code to uppercase for API matching
    df["geo_code"] = df["geo_code"].str.upper().str.strip()

    df.to_csv("output/top25_countries.csv", index=False)
    print(f"✓ {len(df)} countries saved to output/top25_countries.csv")
    return df


# ── Step 3: Top placement types by publisher spend ────────────────────────────

def fetch_top_placement_types(token):
    print("\nFetching placement types by publisher spend...")

    data = run_looker_query(
        token,
        model="dmx_reports",
        explore="publisher_report",
        fields=[
            "placements.placement_type",
            "publisher_report.unified_ad_spend"
        ],
        filters={
            "publisher_report.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
        },
        sorts=["publisher_report.unified_ad_spend desc"],
        limit=50
    )

    df = pd.DataFrame(data)
    df.columns = ["placement_type_looker", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS

    # Looker label → API value mapping (based on the case statement you shared)
    looker_to_api = {
        "Rewarded": "rewarded",
        "MREC": "mrec",
        "Banner": "banner",
        "Native": "native",
        "Interstitial": "interstitial",
        "In-Line": "in_line",
        "AppOpen": "appopen"
    }
    df["placement_type_api"] = df["placement_type_looker"].map(looker_to_api).fillna(
        df["placement_type_looker"].str.lower()
    )

    # Calculate % of total
    total = df["total_spend_7d"].sum()
    df["pct_of_network"] = (df["total_spend_7d"] / total * 100).round(2)
    df["rank"] = range(1, len(df) + 1)

    df.to_csv("output/top_placement_types.csv", index=False)
    print(f"✓ {len(df)} placement types saved to output/top_placement_types.csv")
    return df


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing LOOKER_CLIENT_ID or LOOKER_CLIENT_SECRET in .env")

    token = get_looker_token()
    rtb_df = fetch_rtb_accounts(token)
    countries_df = fetch_top_countries(token)
    placements_df = fetch_top_placement_types(token)

    print(f"\n{'='*50}")
    print(f"Part 1b complete!")
    print(f"  RTB accounts to audit: {rtb_df['rtb_account_id'].nunique()}")
    print(f"  RTB connections to call API for: {len(rtb_df)}")
    print(f"  Top countries: {len(countries_df)}")
    print(f"  Placement types: {len(placements_df)}")
    print(f"  Next: run part2b_build_geo_placement_audit.py")
    print(f"{'='*50}")