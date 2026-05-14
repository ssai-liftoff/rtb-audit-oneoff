"""
Publisher Creative ID Blocks Audit
Queries 1,2,4 use vx_analytics/vx_overview for spend.
Queries 3,5 use dmx_reports/publisher_report for blocklists (faster).

Outputs:
  - output/audit_pub_account_blocked_creatives.csv
  - output/audit_pub_app_blocked_creatives.csv
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


def run_vx_query(token, fields, filters, sorts=None, limit=10000):
    payload = {
        "model": "vx_analytics",
        "view": "vx_overview",
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


def run_pub_report_query(token, fields, filters, limit=10000):
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model": "dmx_reports",
            "view": "publisher_report",
            "fields": fields,
            "filters": filters,
            "limit": str(limit)
        }
    )
    resp.raise_for_status()
    return resp.json()


# ── Query 1: Top spending creatives ──────────────────────────────────────────

def fetch_top_creatives(token):
    print("\n[1/5] Fetching top spending creatives...")
    data = run_vx_query(
        token,
        fields=["vx_overview.creative_id", "vx_overview.adomain", "vx_overview.unified_ad_spend"],
        filters={"vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"},
        sorts=["vx_overview.unified_ad_spend desc"]
    )
    df = pd.DataFrame(data)
    df.columns = ["creative_id", "adomain", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df[df["daily_spend"] >= SPEND_THRESHOLD].copy()
    df = df[df["creative_id"].notna() & (df["creative_id"] != "")].copy()
    print(f"  ✓ {len(df)} high-spend creatives (>${SPEND_THRESHOLD}/day)")
    return df


# ── Query 2: Publisher account spend ─────────────────────────────────────────

def fetch_pub_account_spend(token):
    print("\n[2/5] Fetching publisher account spend...")
    data = run_vx_query(
        token,
        fields=["publisher_accounts.id", "publisher_accounts.name", "vx_overview.unified_ad_spend"],
        filters={"vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"},
        sorts=["vx_overview.unified_ad_spend desc"]
    )
    df = pd.DataFrame(data)
    df.columns = ["publisher_account_id", "publisher_account_name", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df[df["daily_spend"] >= SPEND_THRESHOLD].copy()
    df = df[df["publisher_account_id"].notna() & (df["publisher_account_id"] != "")].copy()
    print(f"  ✓ {len(df)} high-spend publisher accounts (>${SPEND_THRESHOLD}/day)")
    return df


# ── Query 3: Publisher account blocklists via publisher_report ────────────────

def fetch_account_blocklists(token, high_spend_account_ids):
    print("\n[3/5] Fetching publisher account blocked creative lists (publisher_report)...")

    if not high_spend_account_ids:
        print("  ✓ No high-spend accounts to check")
        return pd.DataFrame(columns=["publisher_account_id", "publisher_account_name", "blocked_creatives_raw"])

    data = run_pub_report_query(
        token,
        fields=["publisher_accounts.id", "publisher_accounts.name", "publisher_accounts.blocked_edsp_creatives"],
        filters={
            "publisher_accounts.id": ",".join(high_spend_account_ids),
            "publisher_accounts.blocked_edsp_creatives": "-NULL,-empty",
            "publisher_report.event_date": "1 days"
        }
    )
    df = pd.DataFrame(data)
    df.columns = ["publisher_account_id", "publisher_account_name", "blocked_creatives_raw"]
    df = df[df["blocked_creatives_raw"].notna() & (df["blocked_creatives_raw"] != "")].copy()
    df = df.drop_duplicates(subset=["publisher_account_id"]).copy()
    print(f"  ✓ {len(df)} publisher accounts with creative blocks")
    return df


# ── Query 4: Publisher app spend ──────────────────────────────────────────────

def fetch_pub_app_spend(token):
    print("\n[4/5] Fetching publisher app spend...")
    data = run_vx_query(
        token,
        fields=["publisher_apps.id", "publisher_apps.name", "publisher_accounts.id", "publisher_accounts.name", "vx_overview.unified_ad_spend"],
        filters={"vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"},
        sorts=["vx_overview.unified_ad_spend desc"]
    )
    df = pd.DataFrame(data)
    df.columns = ["publisher_app_id", "publisher_app_name", "publisher_account_id", "publisher_account_name", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df[df["daily_spend"] >= SPEND_THRESHOLD].copy()
    df = df[df["publisher_app_id"].notna() & (df["publisher_app_id"] != "")].copy()
    print(f"  ✓ {len(df)} high-spend publisher apps (>${SPEND_THRESHOLD}/day)")
    return df


# ── Query 5: Publisher app blocklists via publisher_report ────────────────────

def fetch_app_blocklists(token, high_spend_app_ids):
    print("\n[5/5] Fetching publisher app blocked creative lists (publisher_report)...")

    if not high_spend_app_ids:
        print("  ✓ No high-spend apps to check")
        return pd.DataFrame(columns=["publisher_app_id", "publisher_app_name", "publisher_account_id", "publisher_account_name", "blocked_creatives_raw"])

    data = run_pub_report_query(
        token,
        fields=["publisher_apps.id", "publisher_apps.name", "publisher_accounts.id", "publisher_accounts.name", "publisher_apps.blocked_edsp_creatives"],
        filters={
            "publisher_apps.id": ",".join(high_spend_app_ids),
            "publisher_apps.blocked_edsp_creatives": "-NULL,-empty",
            "publisher_report.event_date": "1 days"
        }
    )
    df = pd.DataFrame(data)
    df.columns = ["publisher_app_id", "publisher_app_name", "publisher_account_id", "publisher_account_name", "blocked_creatives_raw"]
    df = df[df["blocked_creatives_raw"].notna() & (df["blocked_creatives_raw"] != "")].copy()
    df = df.drop_duplicates(subset=["publisher_app_id"]).copy()
    print(f"  ✓ {len(df)} publisher apps with creative blocks")
    return df


# ── Build account-level audit ─────────────────────────────────────────────────

def build_account_audit(account_spend_df, account_blocklist_df, creatives_df):
    print("\nBuilding account-level audit...")

    total_creative_spend = creatives_df["daily_spend"].sum()
    high_spend_creatives = set(creatives_df["creative_id"])
    creative_lookup = creatives_df.set_index("creative_id")[["adomain", "daily_spend"]].to_dict("index")
    account_spend_lookup = account_spend_df.set_index("publisher_account_id")["daily_spend"].to_dict()

    rows = []
    for _, row in account_blocklist_df.iterrows():
        pub_id = row["publisher_account_id"]
        pub_daily_spend = account_spend_lookup.get(pub_id, 0)
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

def build_app_audit(app_spend_df, app_blocklist_df, creatives_df):
    print("Building app-level audit...")

    total_creative_spend = creatives_df["daily_spend"].sum()
    high_spend_creatives = set(creatives_df["creative_id"])
    creative_lookup = creatives_df.set_index("creative_id")[["adomain", "daily_spend"]].to_dict("index")
    app_spend_lookup = app_spend_df.set_index("publisher_app_id")[["daily_spend", "publisher_account_id", "publisher_account_name"]].to_dict("index")

    rows = []
    for _, row in app_blocklist_df.iterrows():
        app_id = row["publisher_app_id"]
        app_info = app_spend_lookup.get(app_id, {})
        app_daily_spend = app_info.get("daily_spend", 0)
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

    creatives_df = fetch_top_creatives(token)
    account_spend_df = fetch_pub_account_spend(token)
    app_spend_df = fetch_pub_app_spend(token)

    high_spend_account_ids = list(account_spend_df["publisher_account_id"].astype(str))
    high_spend_app_ids = list(app_spend_df["publisher_app_id"].astype(str))

    account_blocklist_df = fetch_account_blocklists(token, high_spend_account_ids)
    app_blocklist_df = fetch_app_blocklists(token, high_spend_app_ids)

    account_output = build_account_audit(account_spend_df, account_blocklist_df, creatives_df)
    app_output = build_app_audit(app_spend_df, app_blocklist_df, creatives_df)

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