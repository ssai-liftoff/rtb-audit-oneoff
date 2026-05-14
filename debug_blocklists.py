"""
Debug: Dump raw eDSPCreativeIdBlocklist for top spending accounts and apps.
Saves full exploded lists BEFORE any creative spend filtering.

Outputs:
  - output/debug_account_blocklists.csv
  - output/debug_app_blocklists.csv
"""

import os
import time
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")
CREATIVE_API_TOKEN = os.getenv("CREATIVE_API_TOKEN")

GRAPHQL_URL = "https://pub-gateway-api.vungle.com/query"
GRAPHQL_HEADERS = {
    "authorization": f"Bearer {CREATIVE_API_TOKEN}",
    "content-type": "application/json",
    "accept": "application/json, text/plain, */*",
    "vungle-source": "admin",
    "vungle-version": "1",
    "origin": "https://pubadmin.vungle.com",
    "referer": "https://pubadmin.vungle.com/"
}

ACCOUNT_QUERY = """
query account($id: String!) {
  account(id: $id) {
    id
    name
    eDSPCreativeIdBlocklist
  }
}
"""

APP_QUERY = """
query application($id: String!) {
  application(id: $id) {
    id
    name
    owner
    eDSPCreativeIdBlocklist
  }
}
"""

LOOKBACK_DAYS = 7
SPEND_THRESHOLD = 1000

# Limit to top N to keep debug fast - increase if needed
DEBUG_TOP_N_ACCOUNTS = 20
DEBUG_TOP_N_APPS = 20

os.makedirs("output", exist_ok=True)


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


def clean_blocklist(raw_list):
    if not raw_list:
        return []
    return [x.strip().rstrip(",").strip() for x in raw_list if x and x.strip()]


def fetch_top_accounts(token):
    print(f"\nFetching top {DEBUG_TOP_N_ACCOUNTS} spending accounts...")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model": "vx_analytics",
            "view": "vx_overview",
            "fields": ["publisher_accounts.id", "publisher_accounts.name", "vx_overview.unified_ad_spend"],
            "filters": {"vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"},
            "sorts": ["vx_overview.unified_ad_spend desc"],
            "limit": str(DEBUG_TOP_N_ACCOUNTS)
        }
    )
    resp.raise_for_status()
    df = pd.DataFrame(resp.json())
    df.columns = ["account_id", "account_name", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df[df["account_id"].notna() & (df["account_id"] != "")].copy()
    print(f"  ✓ {len(df)} accounts")
    return df


def fetch_top_apps(token):
    print(f"\nFetching top {DEBUG_TOP_N_APPS} spending apps...")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model": "vx_analytics",
            "view": "vx_overview",
            "fields": [
                "publisher_apps.id", "publisher_apps.name",
                "publisher_accounts.id", "publisher_accounts.name",
                "vx_overview.unified_ad_spend"
            ],
            "filters": {"vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"},
            "sorts": ["vx_overview.unified_ad_spend desc"],
            "limit": str(DEBUG_TOP_N_APPS)
        }
    )
    resp.raise_for_status()
    df = pd.DataFrame(resp.json())
    df.columns = ["app_id", "app_name", "account_id", "account_name", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df[df["app_id"].notna() & (df["app_id"] != "")].copy()
    print(f"  ✓ {len(df)} apps")
    return df


def fetch_top_creatives(token):
    print(f"\nFetching top spending creatives (>${SPEND_THRESHOLD}/day)...")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model": "vx_analytics",
            "view": "vx_overview",
            "fields": ["vx_overview.creative_id", "vx_overview.adomain", "vx_overview.unified_ad_spend"],
            "filters": {"vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"},
            "sorts": ["vx_overview.unified_ad_spend desc"],
            "limit": "10000"
        }
    )
    resp.raise_for_status()
    df = pd.DataFrame(resp.json())
    df.columns = ["creative_id", "adomain", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df[df["daily_spend"] >= SPEND_THRESHOLD].copy()
    df = df[df["creative_id"].notna() & (df["creative_id"] != "")].copy()
    print(f"  ✓ {len(df)} high-spend creatives")
    return df


# ── Dump account blocklists ───────────────────────────────────────────────────

def dump_account_blocklists(accounts_df, creatives_df):
    high_spend_creatives = set(creatives_df["creative_id"])
    rows = []

    print(f"\n── Fetching account blocklists ──")
    for i, row in accounts_df.iterrows():
        account_id = str(row["account_id"])
        print(f"  [{i+1}/{len(accounts_df)}] {row['account_name']} ({account_id})")

        try:
            resp = requests.post(
                GRAPHQL_URL,
                headers=GRAPHQL_HEADERS,
                json={
                    "operationName": "account",
                    "variables": {"id": account_id, "page": 0, "perPage": 0},
                    "query": ACCOUNT_QUERY
                },
                timeout=15
            )
            resp.raise_for_status()
            raw = resp.json().get("data", {}).get("account", {}).get("eDSPCreativeIdBlocklist", [])
            blocklist = clean_blocklist(raw)
        except Exception as e:
            print(f"    ⚠ Error: {e}")
            blocklist = []

        print(f"    → {len(blocklist)} total blocked creative IDs")

        for creative_id in blocklist:
            rows.append({
                "account_id": account_id,
                "account_name": row["account_name"],
                "account_daily_spend": round(row["daily_spend"], 2),
                "blocked_creative_id": creative_id,
                "is_high_spend_creative": creative_id in high_spend_creatives
            })

        time.sleep(0.1)

    output = pd.DataFrame(rows)
    output.to_csv("output/debug_account_blocklists.csv", index=False)

    total = len(output)
    matched = output["is_high_spend_creative"].sum() if not output.empty else 0
    print(f"\n✓ Saved {total} rows to output/debug_account_blocklists.csv")
    print(f"  Total blocked creative IDs: {total}")
    print(f"  Matching high-spend creatives: {matched}")
    if matched == 0 and total > 0:
        print(f"  ⚠ No overlap found — sample of blocked IDs:")
        print(output["blocked_creative_id"].head(10).to_string())
        print(f"\n  Sample of high-spend creative IDs:")
        print(creatives_df["creative_id"].head(10).to_string())
    return output


# ── Dump app blocklists ───────────────────────────────────────────────────────

def dump_app_blocklists(apps_df, creatives_df):
    high_spend_creatives = set(creatives_df["creative_id"])
    rows = []

    print(f"\n── Fetching app blocklists ──")
    for i, row in apps_df.iterrows():
        app_id = str(row["app_id"])
        print(f"  [{i+1}/{len(apps_df)}] {row['app_name']} ({app_id})")

        try:
            resp = requests.post(
                GRAPHQL_URL,
                headers=GRAPHQL_HEADERS,
                json={
                    "operationName": "application",
                    "variables": {"reportIncluded": False, "id": app_id},
                    "query": APP_QUERY
                },
                timeout=15
            )
            resp.raise_for_status()
            raw = resp.json().get("data", {}).get("application", {}).get("eDSPCreativeIdBlocklist", [])
            blocklist = clean_blocklist(raw)
        except Exception as e:
            print(f"    ⚠ Error: {e}")
            blocklist = []

        print(f"    → {len(blocklist)} total blocked creative IDs")

        for creative_id in blocklist:
            rows.append({
                "app_id": app_id,
                "app_name": row["app_name"],
                "account_id": row["account_id"],
                "account_name": row["account_name"],
                "app_daily_spend": round(row["daily_spend"], 2),
                "blocked_creative_id": creative_id,
                "is_high_spend_creative": creative_id in high_spend_creatives
            })

        time.sleep(0.1)

    output = pd.DataFrame(rows)
    output.to_csv("output/debug_app_blocklists.csv", index=False)

    total = len(output)
    matched = output["is_high_spend_creative"].sum() if not output.empty else 0
    print(f"\n✓ Saved {total} rows to output/debug_app_blocklists.csv")
    print(f"  Total blocked creative IDs: {total}")
    print(f"  Matching high-spend creatives: {matched}")
    if matched == 0 and total > 0:
        print(f"  ⚠ No overlap found — sample of blocked IDs:")
        print(output["blocked_creative_id"].head(10).to_string())
        print(f"\n  Sample of high-spend creative IDs:")
        print(creatives_df["creative_id"].head(10).to_string())
    return output


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")
    if not CREATIVE_API_TOKEN:
        raise ValueError("Missing CREATIVE_API_TOKEN in .env")

    looker_token = get_looker_token()
    accounts_df = fetch_top_accounts(looker_token)
    apps_df = fetch_top_apps(looker_token)
    creatives_df = fetch_top_creatives(looker_token)

    account_debug = dump_account_blocklists(accounts_df, creatives_df)
    app_debug = dump_app_blocklists(apps_df, creatives_df)

    print(f"\n{'='*50}")
    print(f"Debug complete!")
    print(f"  Check output/debug_account_blocklists.csv")
    print(f"  Check output/debug_app_blocklists.csv")
    print(f"  is_high_spend_creative=True rows show where overlap exists")
    print(f"{'='*50}")