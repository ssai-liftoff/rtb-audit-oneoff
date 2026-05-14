"""
Publisher Creative ID Blocks Audit - Simplified Parallel Version
Just 2 queries run simultaneously:
  1. High-spend creatives (vx_analytics/vx_overview)
  2a. Publisher account blocklists with spend filter (dmx_reports/publisher_report)
  2b. Publisher app blocklists with spend filter (dmx_reports/publisher_report)

Outputs:
  - output/audit_pub_account_blocked_creatives.csv
  - output/audit_pub_app_blocked_creatives.csv
"""

import os
import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

LOOKBACK_DAYS = 7
SPEND_THRESHOLD_7D = 7000   # total over 7 days = $1000/day equivalent
CREATIVE_SPEND_THRESHOLD = 1000  # per day for creatives

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


# ── Query A: High-spend creatives (vx_overview) ───────────────────────────────

def fetch_top_creatives(token):
    print("[A] Fetching high-spend creatives (vx_overview)...")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model": "vx_analytics",
            "view": "vx_overview",
            "fields": [
                "vx_overview.creative_id",
                "vx_overview.adomain",
                "vx_overview.unified_ad_spend"
            ],
            "filters": {
                "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
            },
            "sorts": ["vx_overview.unified_ad_spend desc"],
            "limit": "10000"
        }
    )
    resp.raise_for_status()
    data = resp.json()
    df = pd.DataFrame(data)
    df.columns = ["creative_id", "adomain", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df[df["daily_spend"] >= CREATIVE_SPEND_THRESHOLD].copy()
    df = df[df["creative_id"].notna() & (df["creative_id"] != "")].copy()
    print(f"  ✓ [A] {len(df)} high-spend creatives (>${CREATIVE_SPEND_THRESHOLD}/day)")
    return df


# ── Query B: Publisher account blocklists with spend filter ───────────────────

def fetch_account_blocklists(token):
    print("[B] Fetching publisher account blocked creatives (publisher_report)...")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model": "dmx_reports",
            "view": "publisher_report",
            "fields": [
                "publisher_accounts.id",
                "publisher_accounts.name",
                "publisher_accounts.blocked_edsp_creatives",
                "publisher_report.unified_ad_spend"
            ],
            "filters": {
                "publisher_report.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
                "publisher_accounts.blocked_edsp_creatives": "-NULL,-empty",
                "publisher_report.unified_ad_spend": f">={SPEND_THRESHOLD_7D}"
            },
            "limit": "10000"
        }
    )
    resp.raise_for_status()
    data = resp.json()
    df = pd.DataFrame(data)
    df.columns = ["publisher_account_id", "publisher_account_name", "blocked_creatives_raw", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df[df["blocked_creatives_raw"].notna() & (df["blocked_creatives_raw"] != "")].copy()
    df = df.drop_duplicates(subset=["publisher_account_id"]).copy()
    print(f"  ✓ [B] {len(df)} publisher accounts with blocks and >${SPEND_THRESHOLD_7D/LOOKBACK_DAYS:.0f}/day spend")
    return df


# ── Query C: Publisher app blocklists with spend filter ───────────────────────

def fetch_app_blocklists(token):
    print("[C] Fetching publisher app blocked creatives (publisher_report)...")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model": "dmx_reports",
            "view": "publisher_report",
            "fields": [
                "publisher_apps.id",
                "publisher_apps.name",
                "publisher_accounts.id",
                "publisher_accounts.name",
                "publisher_apps.blocked_edsp_creatives",
                "publisher_report.unified_ad_spend"
            ],
            "filters": {
                "publisher_report.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
                "publisher_apps.blocked_edsp_creatives": "-NULL,-empty",
                "publisher_report.unified_ad_spend": f">={SPEND_THRESHOLD_7D}"
            },
            "limit": "10000"
        }
    )
    resp.raise_for_status()
    data = resp.json()
    df = pd.DataFrame(data)
    df.columns = ["publisher_app_id", "publisher_app_name", "publisher_account_id", "publisher_account_name", "blocked_creatives_raw", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df[df["blocked_creatives_raw"].notna() & (df["blocked_creatives_raw"] != "")].copy()
    df = df.drop_duplicates(subset=["publisher_app_id"]).copy()
    print(f"  ✓ [C] {len(df)} publisher apps with blocks and >${SPEND_THRESHOLD_7D/LOOKBACK_DAYS:.0f}/day spend")
    return df


# ── Build account-level audit ─────────────────────────────────────────────────

def build_account_audit(account_blocklist_df, creatives_df):
    print("\nBuilding account-level audit...")

    total_creative_spend = creatives_df["daily_spend"].sum()
    high_spend_creatives = set(creatives_df["creative_id"])
    creative_lookup = creatives_df.set_index("creative_id")[["adomain", "daily_spend"]].to_dict("index")

    rows = []
    for _, row in account_blocklist_df.iterrows():
        pub_id = row["publisher_account_id"]
        pub_daily_spend = row["daily_spend"]
        blocked_ids = [x.strip() for x in str(row["blocked_creatives_raw"]).split(",") if x.strip()]

        for creative_id in blocked_ids:
            if creative_id not in high_spend_creatives:
                continue
            creative_info = creative_lookup.get(creative_id, {})
            creative_daily_spend = creative_info.get("daily_spend", 0)
            adomain = creative_info.get("adomain", "")
            pct_of_network = creative_daily_spend / total_creative_spend if total_creative_spend > 0 else 0
            rows.append({
                "publisher_account_id": pub_id,
                "publisher_account_name": row["publisher_account_name"],
                "publisher_daily_spend": round(pub_daily_spend, 2),
                "blocked_creative_id": creative_id,
                "creative_adomain": adomain,
                "creative_daily_spend": round(creative_daily_spend, 2),
                "creative_pct_of_network": round(pct_of_network * 100, 2),
                "potential_uplift": round(pct_of_network * pub_daily_spend, 2)
            })

    output = pd.DataFrame(rows)
    if not output.empty:
        output = output.sort_values("potential_uplift", ascending=False).reset_index(drop=True)
    output.to_csv("output/audit_pub_account_blocked_creatives.csv", index=False)
    print(f"✓ {len(output)} account-level opportunities → output/audit_pub_account_blocked_creatives.csv")
    return output


# ── Build app-level audit ─────────────────────────────────────────────────────

def build_app_audit(app_blocklist_df, creatives_df):
    print("Building app-level audit...")

    total_creative_spend = creatives_df["daily_spend"].sum()
    high_spend_creatives = set(creatives_df["creative_id"])
    creative_lookup = creatives_df.set_index("creative_id")[["adomain", "daily_spend"]].to_dict("index")

    rows = []
    for _, row in app_blocklist_df.iterrows():
        app_id = row["publisher_app_id"]
        app_daily_spend = row["daily_spend"]
        blocked_ids = [x.strip() for x in str(row["blocked_creatives_raw"]).split(",") if x.strip()]

        for creative_id in blocked_ids:
            if creative_id not in high_spend_creatives:
                continue
            creative_info = creative_lookup.get(creative_id, {})
            creative_daily_spend = creative_info.get("daily_spend", 0)
            adomain = creative_info.get("adomain", "")
            pct_of_network = creative_daily_spend / total_creative_spend if total_creative_spend > 0 else 0
            rows.append({
                "publisher_app_id": app_id,
                "publisher_app_name": row["publisher_app_name"],
                "publisher_account_id": row["publisher_account_id"],
                "publisher_account_name": row["publisher_account_name"],
                "app_daily_spend": round(app_daily_spend, 2),
                "blocked_creative_id": creative_id,
                "creative_adomain": adomain,
                "creative_daily_spend": round(creative_daily_spend, 2),
                "creative_pct_of_network": round(pct_of_network * 100, 2),
                "potential_uplift": round(pct_of_network * app_daily_spend, 2)
            })

    output = pd.DataFrame(rows)
    if not output.empty:
        output = output.sort_values("potential_uplift", ascending=False).reset_index(drop=True)
    output.to_csv("output/audit_pub_app_blocked_creatives.csv", index=False)
    print(f"✓ {len(output)} app-level opportunities → output/audit_pub_app_blocked_creatives.csv")
    return output


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    token = get_looker_token()

    # Run all 3 queries in parallel
    print("\nRunning 3 queries in parallel...")
    results = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(fetch_top_creatives, token): "creatives",
            executor.submit(fetch_account_blocklists, token): "account_blocks",
            executor.submit(fetch_app_blocklists, token): "app_blocks"
        }
        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
            except Exception as e:
                print(f"  ✗ Query '{key}' failed: {e}")
                raise

    creatives_df = results["creatives"]
    account_blocklist_df = results["account_blocks"]
    app_blocklist_df = results["app_blocks"]

    account_output = build_account_audit(account_blocklist_df, creatives_df)
    app_output = build_app_audit(app_blocklist_df, creatives_df)

    print(f"\n{'='*50}")
    print(f"Audit complete!")
    print(f"  Account-level opportunities: {len(account_output)}")
    print(f"  App-level opportunities: {len(app_output)}")
    if not account_output.empty:
        print(f"\nTop 5 account-level:")
        print(account_output[["publisher_account_name", "blocked_creative_id", "creative_adomain", "potential_uplift"]].head().to_string())
    if not app_output.empty:
        print(f"\nTop 5 app-level:")
        print(app_output[["publisher_app_name", "blocked_creative_id", "creative_adomain", "potential_uplift"]].head().to_string())
    print(f"{'='*50}")