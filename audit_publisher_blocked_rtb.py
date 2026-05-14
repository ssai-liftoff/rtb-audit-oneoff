"""
Publisher Blocking RTB Audit
Fetches publishers blocking high-spend RTB accounts and connections.

Outputs:
  - output/audit_publisher_blocked_rtb_accounts.csv
  - output/audit_publisher_blocked_rtb_connections.csv
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
SPEND_THRESHOLD = 1000

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


def run_looker_query(token, fields, filters, limit=50000):
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model": "vx_analytics",
            "view": "vx_overview",
            "fields": fields,
            "filters": filters,
            "limit": str(limit)
        }
    )
    resp.raise_for_status()
    return resp.json()


# ── Step 1: Fetch all data from Looker ───────────────────────────────────────

def fetch_data(token):
    print("\nFetching publisher + RTB spend and block data from Looker...")

    data = run_looker_query(
        token,
        fields=[
            "publisher_accounts.id",
            "publisher_accounts.name",
            "publisher_accounts.blocked_rtb_accounts",
            "publisher_accounts.blocked_rtb_connections",
            "rtb_accounts.id",
            "rtb_accounts.name",
            "rtb_connections.id",
            "rtb_connections.name",
            "vx_overview.unified_ad_spend"
        ],
        filters={
            "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
        }
    )

    df = pd.DataFrame(data)
    df.columns = [
        "publisher_account_id", "publisher_account_name",
        "blocked_rtb_accounts_raw", "blocked_rtb_connections_raw",
        "rtb_account_id", "rtb_account_name",
        "rtb_connection_id", "rtb_connection_name",
        "total_spend_7d"
    ]

    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS

    print(f"✓ {len(df)} rows fetched")
    return df


# ── Step 2: Build spend lookup tables ────────────────────────────────────────

def build_spend_lookups(df):
    # Publisher daily spend
    pub_spend = df.groupby(
        ["publisher_account_id", "publisher_account_name"]
    )["daily_spend"].sum().reset_index()
    pub_spend.columns = ["publisher_account_id", "publisher_account_name", "publisher_daily_spend"]

    # RTB account daily spend
    rtb_account_spend = df.groupby(
        ["rtb_account_id", "rtb_account_name"]
    )["daily_spend"].sum().reset_index()
    rtb_account_spend.columns = ["rtb_account_id", "rtb_account_name", "rtb_account_daily_spend"]

    # RTB connection daily spend
    rtb_connection_spend = df.groupby(
        ["rtb_connection_id", "rtb_connection_name"]
    )["daily_spend"].sum().reset_index()
    rtb_connection_spend.columns = ["rtb_connection_id", "rtb_connection_name", "rtb_connection_daily_spend"]

    # Network totals for uplift
    total_rtb_account_spend = rtb_account_spend["rtb_account_daily_spend"].sum()
    total_rtb_connection_spend = rtb_connection_spend["rtb_connection_daily_spend"].sum()

    # Filter to high-spend only
    high_pub = set(pub_spend[pub_spend["publisher_daily_spend"] >= SPEND_THRESHOLD]["publisher_account_id"])
    high_rtb_accounts = set(rtb_account_spend[rtb_account_spend["rtb_account_daily_spend"] >= SPEND_THRESHOLD]["rtb_account_id"])
    high_rtb_connections = set(rtb_connection_spend[rtb_connection_spend["rtb_connection_daily_spend"] >= SPEND_THRESHOLD]["rtb_connection_id"])

    print(f"  High-spend publishers: {len(high_pub)}")
    print(f"  High-spend RTB accounts: {len(high_rtb_accounts)}")
    print(f"  High-spend RTB connections: {len(high_rtb_connections)}")

    return {
        "pub_spend": pub_spend.set_index("publisher_account_id").to_dict("index"),
        "rtb_account_spend": rtb_account_spend.set_index("rtb_account_id").to_dict("index"),
        "rtb_connection_spend": rtb_connection_spend.set_index("rtb_connection_id").to_dict("index"),
        "total_rtb_account_spend": total_rtb_account_spend,
        "total_rtb_connection_spend": total_rtb_connection_spend,
        "high_pub": high_pub,
        "high_rtb_accounts": high_rtb_accounts,
        "high_rtb_connections": high_rtb_connections
    }


# ── Step 3: Build blocked RTB accounts audit ─────────────────────────────────

def build_blocked_accounts_audit(df, lookups):
    print("\nBuilding blocked RTB accounts audit...")

    # Get unique publisher → blocked_rtb_accounts mapping
    pub_blocks = df[["publisher_account_id", "publisher_account_name", "blocked_rtb_accounts_raw"]].drop_duplicates()
    pub_blocks = pub_blocks[pub_blocks["blocked_rtb_accounts_raw"].notna()]
    pub_blocks = pub_blocks[pub_blocks["blocked_rtb_accounts_raw"] != ""]

    rows = []
    for _, row in pub_blocks.iterrows():
        pub_id = row["publisher_account_id"]

        # Skip low-spend publishers
        if pub_id not in lookups["high_pub"]:
            continue

        pub_info = lookups["pub_spend"].get(pub_id, {})
        pub_daily_spend = pub_info.get("publisher_daily_spend", 0)

        # Explode comma-separated blocked RTB account IDs
        blocked_ids = [x.strip() for x in str(row["blocked_rtb_accounts_raw"]).split(",") if x.strip()]

        for blocked_id in blocked_ids:
            # Skip low-spend RTB accounts
            if blocked_id not in lookups["high_rtb_accounts"]:
                continue

            rtb_info = lookups["rtb_account_spend"].get(blocked_id, {})
            rtb_daily_spend = rtb_info.get("rtb_account_daily_spend", 0)
            rtb_name = rtb_info.get("rtb_account_name", "")

            # Uplift = (blocked RTB spend / total RTB network spend) × publisher daily spend
            pct_of_network = rtb_daily_spend / lookups["total_rtb_account_spend"] if lookups["total_rtb_account_spend"] > 0 else 0
            uplift = pct_of_network * pub_daily_spend

            rows.append({
                "publisher_account_id": pub_id,
                "publisher_account_name": row["publisher_account_name"],
                "publisher_daily_spend": round(pub_daily_spend, 2),
                "blocked_rtb_account_id": blocked_id,
                "blocked_rtb_account_name": rtb_name,
                "rtb_account_daily_spend": round(rtb_daily_spend, 2),
                "rtb_pct_of_network": round(pct_of_network * 100, 2),
                "potential_uplift": round(uplift, 2)
            })

    output = pd.DataFrame(rows)
    if not output.empty:
        output = output.sort_values("potential_uplift", ascending=False).reset_index(drop=True)

    output.to_csv("output/audit_publisher_blocked_rtb_accounts.csv", index=False)
    print(f"✓ {len(output)} opportunities → output/audit_publisher_blocked_rtb_accounts.csv")
    return output


# ── Step 4: Build blocked RTB connections audit ───────────────────────────────

def build_blocked_connections_audit(df, lookups):
    print("Building blocked RTB connections audit...")

    # Get unique publisher → blocked_rtb_connections mapping
    pub_blocks = df[["publisher_account_id", "publisher_account_name", "blocked_rtb_connections_raw"]].drop_duplicates()
    pub_blocks = pub_blocks[pub_blocks["blocked_rtb_connections_raw"].notna()]
    pub_blocks = pub_blocks[pub_blocks["blocked_rtb_connections_raw"] != ""]

    rows = []
    for _, row in pub_blocks.iterrows():
        pub_id = row["publisher_account_id"]

        # Skip low-spend publishers
        if pub_id not in lookups["high_pub"]:
            continue

        pub_info = lookups["pub_spend"].get(pub_id, {})
        pub_daily_spend = pub_info.get("publisher_daily_spend", 0)

        # Explode comma-separated blocked RTB connection IDs
        blocked_ids = [x.strip() for x in str(row["blocked_rtb_connections_raw"]).split(",") if x.strip()]

        for blocked_id in blocked_ids:
            # Skip low-spend RTB connections
            if blocked_id not in lookups["high_rtb_connections"]:
                continue

            rtb_info = lookups["rtb_connection_spend"].get(blocked_id, {})
            rtb_daily_spend = rtb_info.get("rtb_connection_daily_spend", 0)
            rtb_name = rtb_info.get("rtb_connection_name", "")

            # Uplift = (blocked RTB connection spend / total RTB connection network spend) × publisher daily spend
            pct_of_network = rtb_daily_spend / lookups["total_rtb_connection_spend"] if lookups["total_rtb_connection_spend"] > 0 else 0
            uplift = pct_of_network * pub_daily_spend

            rows.append({
                "publisher_account_id": pub_id,
                "publisher_account_name": row["publisher_account_name"],
                "publisher_daily_spend": round(pub_daily_spend, 2),
                "blocked_rtb_connection_id": blocked_id,
                "blocked_rtb_connection_name": rtb_name,
                "rtb_connection_daily_spend": round(rtb_daily_spend, 2),
                "rtb_pct_of_network": round(pct_of_network * 100, 2),
                "potential_uplift": round(uplift, 2)
            })

    output = pd.DataFrame(rows)
    if not output.empty:
        output = output.sort_values("potential_uplift", ascending=False).reset_index(drop=True)

    output.to_csv("output/audit_publisher_blocked_rtb_connections.csv", index=False)
    print(f"✓ {len(output)} opportunities → output/audit_publisher_blocked_rtb_connections.csv")
    return output


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    token = get_looker_token()
    df = fetch_data(token)
    lookups = build_spend_lookups(df)
    accounts_output = build_blocked_accounts_audit(df, lookups)
    connections_output = build_blocked_connections_audit(df, lookups)

    print(f"\n{'='*50}")
    print(f"Audit complete!")
    print(f"  Blocked RTB account opportunities: {len(accounts_output)}")
    print(f"  Blocked RTB connection opportunities: {len(connections_output)}")
    if not accounts_output.empty:
        print(f"\nTop 5 blocked RTB account opportunities:")
        print(accounts_output[["publisher_account_name", "blocked_rtb_account_name", "publisher_daily_spend", "rtb_account_daily_spend", "potential_uplift"]].head().to_string())
    if not connections_output.empty:
        print(f"\nTop 5 blocked RTB connection opportunities:")
        print(connections_output[["publisher_account_name", "blocked_rtb_connection_name", "publisher_daily_spend", "rtb_connection_daily_spend", "potential_uplift"]].head().to_string())
    print(f"{'='*50}")