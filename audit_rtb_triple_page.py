"""
RTB Connection Triple Page Audit
Fetches top spending RTB connections, hits API per connection,
filters for those supporting Interstitial or Rewarded but with allowTriplePage = false.

Output: output/audit_rtb_triple_page.csv
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

RTB_API_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2NvdW50IjoiNjgxM2JmZDUxMDRiNDAwMDExMzcyNDM2IiwiaXNNYW5hZ2VkQWNjb3VudCI6ZmFsc2UsImFwaVRva2VuIjoiN2FhMThlNTdmMTU0NmQ2YmQ1NDJlMDc2YWI5NzI3YzMiLCJlbWFpbCI6InNzYWlAbGlmdG9mZi5pbyIsImZlYXR1cmVzIjpbImZlYXR1cmVfbWVkaWF0aW9uX2FjY2VzcyJdLCJpZCI6IjY4MTNiZmQ1MTA0YjQwMDAxMTM3MjQzYSIsImlzX2RlbGV0ZWQiOmZhbHNlLCJpc0ludGVybmFsIjp0cnVlLCJyb2xlIjoiYWNjb3VudF9zdHJhdGVneSIsInNvdXJjZSI6IiIsInVpZCI6IjAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMCIsImV4cCI6MTc3ODM0MjA3M30._7XilntA69YjJDfCz-rGHMPpUzMOuBC92ectkBFN0oQ"

RTB_API_BASE = "https://pub-ctrl-api.vungle.com/api/v1/rtbconnections"
RTB_HEADERS = {
    "authorization": f"Bearer {RTB_API_TOKEN}",
    "vungle-source": "admin",
    "vungle-version": "1",
    "content-type": "application/json",
    "accept": "application/json, text/plain, */*",
    "origin": "https://pubadmin.vungle.com",
    "referer": "https://pubadmin.vungle.com/"
}

LOOKBACK_DAYS = 7
SPEND_THRESHOLD = 1000
TARGET_PLACEMENTS = {"interstitial", "rewarded"}

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


# ── Fetch RTB connections from Looker ─────────────────────────────────────────

def fetch_rtb_connections(token):
    print("\nFetching top spending RTB connections from Looker...")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model": "vx_analytics",
            "view": "vx_overview",
            "fields": [
                "rtb_connections.id",
                "rtb_connections.name",
                "rtb_accounts.id",
                "rtb_accounts.name",
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
    df = pd.DataFrame(resp.json())
    df.columns = ["connection_id", "connection_name", "account_id", "account_name", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df[df["daily_spend"] >= SPEND_THRESHOLD].copy()
    df = df[df["connection_id"].notna() & (df["connection_id"] != "")].copy()
    print(f"  ✓ {len(df)} connections with >${SPEND_THRESHOLD}/day")
    return df


# ── Fetch connection config from API ──────────────────────────────────────────

def fetch_connection_config(connection_id):
    try:
        resp = requests.get(
            f"{RTB_API_BASE}/{connection_id}",
            headers=RTB_HEADERS,
            timeout=10
        )
        if resp.status_code == 401:
            raise ValueError("RTB API token expired")
        resp.raise_for_status()
        data = resp.json()
        return {
            "supported_placements": [p.lower() for p in data.get("supportedImpressionType", [])],
            "allow_triple_page": data.get("allowTriplePage", False)
        }
    except ValueError:
        raise
    except Exception as e:
        print(f"    ⚠ Error: {e}")
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    token = get_looker_token()
    connections_df = fetch_rtb_connections(token)

    total = len(connections_df)
    rows = []

    print(f"\nChecking {total} connections via API...")

    for i, row in connections_df.iterrows():
        connection_id = str(row["connection_id"])
        print(f"  [{i+1}/{total}] {row['connection_name']} ({connection_id})")

        config = fetch_connection_config(connection_id)
        if config is None:
            continue

        placements = set(config["supported_placements"])
        has_target_placement = bool(placements & TARGET_PLACEMENTS)
        triple_page = config["allow_triple_page"]

        # Only flag connections that have interstitial/rewarded AND triple page is OFF
        if has_target_placement and not triple_page:
            matched_placements = sorted(placements & TARGET_PLACEMENTS)
            print(f"    → FLAGGED | placements: {matched_placements} | triple page: {triple_page}")
            rows.append({
                "connection_id": connection_id,
                "connection_name": row["connection_name"],
                "account_id": row["account_id"],
                "account_name": row["account_name"],
                "daily_spend": round(row["daily_spend"], 2),
                "supported_placements": ", ".join(sorted(placements)),
                "matched_placements": ", ".join(matched_placements),
                "allow_triple_page": triple_page
            })

        time.sleep(0.1)

    output = pd.DataFrame(rows)
    if not output.empty:
        output = output.sort_values("daily_spend", ascending=False).reset_index(drop=True)

    output.to_csv("output/audit_rtb_triple_page.csv", index=False)

    print(f"\n{'='*50}")
    print(f"Audit complete!")
    print(f"  Connections checked: {total}")
    print(f"  Flagged (interstitial/rewarded + triple page OFF): {len(output)}")
    if not output.empty:
        print(f"\nTop 5:")
        print(output[["connection_name", "account_name", "daily_spend", "matched_placements", "allow_triple_page"]].head().to_string())
    print(f"\nSaved to: output/audit_rtb_triple_page.csv")
    print(f"{'='*50}")