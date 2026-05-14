"""
Publisher Creative ID Blocks Audit - Final Version

Key changes:
  - Creative spend from dmx_facts/edsp_transactions__sampled
  - Match key = winner_rtb_accounts.id + "_" + edsp_transactions.creative_id
  - Account + App level via GraphQL API with deduplication
  - Looker data cached to CSV to avoid re-fetching

Outputs:
  - output/audit_account_blocked_creatives.csv
  - output/audit_app_blocked_creatives.csv

Cache files (auto-created, delete to force refresh):
  - output/cache_creatives.csv
  - output/cache_accounts.csv
  - output/cache_apps.csv
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

# Set to False to force re-fetch from Looker (ignores cache files)
USE_CACHE = True

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


def clean_blocklist(raw_list):
    if not raw_list:
        return []
    return [x.strip().rstrip(",").strip() for x in raw_list if x and x.strip()]


# ── Looker queries with caching ───────────────────────────────────────────────

def fetch_top_creatives(token, cache_file="output/cache_creatives.csv"):
    if USE_CACHE and os.path.exists(cache_file):
        print(f"\n[1/3] Loading creatives from cache: {cache_file}")
        return pd.read_csv(cache_file)

    print("\n[1/3] Fetching top spending creatives (edsp_transactions__sampled)...")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model": "dmx_facts",
            "view": "edsp_transactions__sampled",
            "fields": [
                "winner_rtb_accounts.id",
                "edsp_transactions.creative_id",
                "edsp_transactions.unified_ad_spend"
            ],
            "filters": {
                "edsp_transactions.delivery_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
            },
            "sorts": ["edsp_transactions.unified_ad_spend desc"],
            "limit": "50000"
        }
    )
    resp.raise_for_status()
    df = pd.DataFrame(resp.json())
    df.columns = ["rtb_account_id", "creative_id", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS

    # Build the match key: rtb_account_id + "_" + creative_id
    df = df[df["rtb_account_id"].notna() & (df["rtb_account_id"] != "")].copy()
    df = df[df["creative_id"].notna() & (df["creative_id"] != "")].copy()
    df["match_key"] = df["rtb_account_id"].astype(str) + "_" + df["creative_id"].astype(str)

    # Aggregate spend per match_key (in case of duplicates)
    df = df.groupby("match_key").agg(
        rtb_account_id=("rtb_account_id", "first"),
        creative_id=("creative_id", "first"),
        daily_spend=("daily_spend", "sum")
    ).reset_index()

    # Filter to high spend
    df = df[df["daily_spend"] >= SPEND_THRESHOLD].copy()

    df.to_csv(cache_file, index=False)
    print(f"  ✓ {len(df)} high-spend creative match keys (>${SPEND_THRESHOLD}/day) → cached")
    return df


def fetch_top_accounts(token, cache_file="output/cache_accounts.csv"):
    if USE_CACHE and os.path.exists(cache_file):
        print(f"\n[2/3] Loading accounts from cache: {cache_file}")
        return pd.read_csv(cache_file)

    print("\n[2/3] Fetching top spending publisher accounts...")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model": "vx_analytics",
            "view": "vx_overview",
            "fields": ["publisher_accounts.id", "publisher_accounts.name", "vx_overview.unified_ad_spend"],
            "filters": {"vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"},
            "sorts": ["vx_overview.unified_ad_spend desc"],
            "limit": "10000"
        }
    )
    resp.raise_for_status()
    df = pd.DataFrame(resp.json())
    df.columns = ["account_id", "account_name", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df[df["daily_spend"] >= SPEND_THRESHOLD].copy()
    df = df[df["account_id"].notna() & (df["account_id"] != "")].copy()

    df.to_csv(cache_file, index=False)
    print(f"  ✓ {len(df)} high-spend accounts → cached")
    return df


def fetch_top_apps(token, cache_file="output/cache_apps.csv"):
    if USE_CACHE and os.path.exists(cache_file):
        print(f"\n[3/3] Loading apps from cache: {cache_file}")
        return pd.read_csv(cache_file)

    print("\n[3/3] Fetching top spending publisher apps...")
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
            "limit": "10000"
        }
    )
    resp.raise_for_status()
    df = pd.DataFrame(resp.json())
    df.columns = ["app_id", "app_name", "account_id", "account_name", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df[df["daily_spend"] >= SPEND_THRESHOLD].copy()
    df = df[df["app_id"].notna() & (df["app_id"] != "")].copy()

    df.to_csv(cache_file, index=False)
    print(f"  ✓ {len(df)} high-spend apps → cached")
    return df


# ── GraphQL API calls ─────────────────────────────────────────────────────────

def fetch_account_blocklist(account_id):
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
        if resp.status_code == 401:
            raise ValueError("API token expired — refresh CREATIVE_API_TOKEN in .env")
        resp.raise_for_status()
        raw = resp.json().get("data", {}).get("account", {}).get("eDSPCreativeIdBlocklist", [])
        return clean_blocklist(raw)
    except ValueError:
        raise
    except Exception as e:
        print(f"    ⚠ Error fetching account {account_id}: {e}")
        return None


def fetch_app_blocklist(app_id):
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
        if resp.status_code == 401:
            raise ValueError("API token expired — refresh CREATIVE_API_TOKEN in .env")
        resp.raise_for_status()
        raw = resp.json().get("data", {}).get("application", {}).get("eDSPCreativeIdBlocklist", [])
        return clean_blocklist(raw)
    except ValueError:
        raise
    except Exception as e:
        print(f"    ⚠ Error fetching app {app_id}: {e}")
        return None


# ── Build account-level audit ─────────────────────────────────────────────────

def build_account_audit(accounts_df, creatives_df):
    total_creative_spend = creatives_df["daily_spend"].sum()
    high_spend_keys = set(creatives_df["match_key"])
    creative_lookup = creatives_df.set_index("match_key")["daily_spend"].to_dict()

    total = len(accounts_df)
    rows = []
    account_blocks = {}  # account_id → set of raw blocked keys (for app dedup)

    print(f"\n── Account-level audit: {total} accounts ──")

    for i, row in accounts_df.iterrows():
        account_id = str(row["account_id"])
        print(f"  [{i+1}/{total}] {row['account_name']} ({account_id})")

        blocklist = fetch_account_blocklist(account_id)
        if blocklist is None:
            account_blocks[account_id] = set()
            continue

        account_blocks[account_id] = set(blocklist)

        matched = 0
        for match_key in blocklist:
            if match_key not in high_spend_keys:
                continue
            creative_daily_spend = creative_lookup.get(match_key, 0)
            pct_of_network = creative_daily_spend / total_creative_spend if total_creative_spend > 0 else 0
            matched += 1
            rows.append({
                "account_id": account_id,
                "account_name": row["account_name"],
                "account_daily_spend": round(row["daily_spend"], 2),
                "blocked_match_key": match_key,
                "creative_daily_spend": round(creative_daily_spend, 2),
                "creative_pct_of_network": round(pct_of_network * 100, 2),
                "potential_uplift": round(pct_of_network * row["daily_spend"], 2)
            })

        if matched > 0:
            print(f"    → {matched} high-spend creatives blocked")

        time.sleep(0.1)

    output = pd.DataFrame(rows)
    if not output.empty:
        output = output.sort_values("potential_uplift", ascending=False).reset_index(drop=True)
    output.to_csv("output/audit_account_blocked_creatives.csv", index=False)
    print(f"\n✓ {len(output)} account-level opportunities → output/audit_account_blocked_creatives.csv")
    return output, account_blocks


# ── Build app-level audit ─────────────────────────────────────────────────────

def build_app_audit(apps_df, creatives_df, account_blocks):
    total_creative_spend = creatives_df["daily_spend"].sum()
    high_spend_keys = set(creatives_df["match_key"])
    creative_lookup = creatives_df.set_index("match_key")["daily_spend"].to_dict()

    total = len(apps_df)
    rows = []

    print(f"\n── App-level audit: {total} apps ──")

    for i, row in apps_df.iterrows():
        app_id = str(row["app_id"])
        account_id = str(row["account_id"])
        print(f"  [{i+1}/{total}] {row['app_name']} ({app_id})")

        blocklist = fetch_app_blocklist(app_id)
        if blocklist is None or len(blocklist) == 0:
            continue

        # Deduplicate — remove keys already blocked at account level
        account_level_blocks = account_blocks.get(account_id, set())
        app_only_blocks = [k for k in blocklist if k not in account_level_blocks]

        if not app_only_blocks:
            print(f"    → No app-specific blocks (all covered at account level)")
            continue

        matched = 0
        for match_key in app_only_blocks:
            if match_key not in high_spend_keys:
                continue
            creative_daily_spend = creative_lookup.get(match_key, 0)
            pct_of_network = creative_daily_spend / total_creative_spend if total_creative_spend > 0 else 0
            matched += 1
            rows.append({
                "app_id": app_id,
                "app_name": row["app_name"],
                "account_id": account_id,
                "account_name": row["account_name"],
                "app_daily_spend": round(row["daily_spend"], 2),
                "blocked_match_key": match_key,
                "creative_daily_spend": round(creative_daily_spend, 2),
                "creative_pct_of_network": round(pct_of_network * 100, 2),
                "potential_uplift": round(pct_of_network * row["daily_spend"], 2)
            })

        if matched > 0:
            print(f"    → {matched} app-specific high-spend creatives blocked")

        time.sleep(0.1)

    output = pd.DataFrame(rows)
    if not output.empty:
        output = output.sort_values("potential_uplift", ascending=False).reset_index(drop=True)
    output.to_csv("output/audit_app_blocked_creatives.csv", index=False)
    print(f"\n✓ {len(output)} app-level opportunities → output/audit_app_blocked_creatives.csv")
    return output


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")
    if not CREATIVE_API_TOKEN:
        raise ValueError("Missing CREATIVE_API_TOKEN in .env")

    looker_token = get_looker_token()
    creatives_df = fetch_top_creatives(looker_token)
    accounts_df = fetch_top_accounts(looker_token)
    apps_df = fetch_top_apps(looker_token)

    print(f"\n  Match key sample (rtb_account_id_creative_id):")
    print(creatives_df["match_key"].head(5).to_string())

    account_output, account_blocks = build_account_audit(accounts_df, creatives_df)
    app_output = build_app_audit(apps_df, creatives_df, account_blocks)

    print(f"\n{'='*50}")
    print(f"Audit complete!")
    print(f"  Account-level opportunities: {len(account_output)}")
    print(f"  App-level opportunities: {len(app_output)}")
    if not account_output.empty:
        print(f"\nTop 5 account-level:")
        print(account_output[["account_name", "blocked_match_key", "account_daily_spend", "potential_uplift"]].head().to_string())
    if not app_output.empty:
        print(f"\nTop 5 app-level:")
        print(app_output[["app_name", "account_name", "blocked_match_key", "app_daily_spend", "potential_uplift"]].head().to_string())
    print(f"\nTo re-fetch fresh Looker data: set USE_CACHE = False or delete output/cache_*.csv")
    print(f"{'='*50}")