"""
Part 1: Fetch high-spend RTB connections and publisher apps from Looker API
Outputs:
  - output/rtb_connections.csv
  - output/high_spend_apps.csv
"""

import os
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

SPEND_THRESHOLD_DAILY = 10  # $10/day minimum (~$70 over 7 days)
LOOKBACK_DAYS = 7

os.makedirs("output", exist_ok=True)


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_looker_token():
    print("Authenticating with Looker API...")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        data={"client_id": LOOKER_CLIENT_ID, "client_secret": LOOKER_CLIENT_SECRET}
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    print("✓ Authenticated")
    return token


def looker_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


# ── Looker query runner ───────────────────────────────────────────────────────

def run_looker_query(token, model, explore, fields, filters, limit=5000):
    payload = {
        "model": model,
        "view": explore,
        "fields": fields,
        "filters": filters,
        "limit": str(limit)
    }
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json=payload
    )
    resp.raise_for_status()
    return resp.json()


# ── Step 1: RTB connections with spend > $1000/day ───────────────────────────

def fetch_rtb_connections(token):
    print("\nFetching RTB connection spend from Looker (vx_overview)...")

    data = run_looker_query(
        token,
        model="vx_analytics",
        explore="vx_overview",
        fields=[
            "rtb_connections.id",
            "rtb_connections.name",
            "vx_overview.unified_ad_spend"
        ],
        filters={
            "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
        }
    )

    df = pd.DataFrame(data)
    df.columns = ["rtb_connection_id", "rtb_connection_name", "total_spend_7d"]

    # Clean up spend column
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS

    # Filter for > $1000/day
    before = len(df)
    df = df[df["daily_spend"] >= SPEND_THRESHOLD_DAILY].copy()
    print(f"✓ {len(df)} RTB connections with >${SPEND_THRESHOLD_DAILY}/day (filtered from {before})")

    # Drop rows with no connection ID
    df = df[df["rtb_connection_id"].notna() & (df["rtb_connection_id"] != "")].copy()

    df.to_csv("output/rtb_connections.csv", index=False)
    print(f"✓ Saved to output/rtb_connections.csv")
    return df


# ── Step 2: Publisher apps with spend > $1000/day ────────────────────────────

def fetch_high_spend_apps(token):
    print("\nFetching publisher app spend from Looker (publisher_report)...")

    data = run_looker_query(
        token,
        model="dmx_reports",
        explore="publisher_report",
        fields=[
            "publisher_apps.id",
            "publisher_apps.name",
            "publisher_accounts.id",
            "publisher_accounts.name",
            "publisher_report.unified_ad_spend"
        ],
        filters={
            "publisher_report.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
        },
        limit=10000
    )

    df = pd.DataFrame(data)
    df.columns = ["app_id", "app_name", "account_id", "account_name", "total_revenue_7d"]

    # Clean up revenue column
    df["total_revenue_7d"] = pd.to_numeric(df["total_revenue_7d"], errors="coerce").fillna(0)
    df["daily_revenue"] = df["total_revenue_7d"] / LOOKBACK_DAYS

    # Filter for > $1000/day
    before = len(df)
    df = df[df["daily_revenue"] >= SPEND_THRESHOLD_DAILY].copy()
    print(f"✓ {len(df)} apps with >${SPEND_THRESHOLD_DAILY}/day (filtered from {before})")

    # Drop rows with no app ID
    df = df[df["app_id"].notna() & (df["app_id"] != "")].copy()

    df.to_csv("output/high_spend_apps.csv", index=False)
    print(f"✓ Saved to output/high_spend_apps.csv")
    return df


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing LOOKER_CLIENT_ID or LOOKER_CLIENT_SECRET in .env")

    token = get_looker_token()
    rtb_df = fetch_rtb_connections(token)
    apps_df = fetch_high_spend_apps(token)

    print(f"\n{'='*50}")
    print(f"Part 1 complete!")
    print(f"  RTB connections to audit: {len(rtb_df)}")
    print(f"  High-spend apps to check: {len(apps_df)}")
    print(f"  Next: fill RTB_API_TOKEN in .env and run part2_build_audit.py")
    print(f"{'='*50}")