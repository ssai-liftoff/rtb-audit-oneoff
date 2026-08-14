"""
Quick count of total unique rows in blocklist dataset using Looker count measure.
"""

import requests
import pandas as pd
from dotenv import load_dotenv
import os

load_dotenv()

BASE = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
CLIENT_ID = os.getenv("LOOKER_CLIENT_ID")
CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

BASE_FILTERS = {
    "vungle_sdk.has_vungle_sdk": "Yes",
    "advertiser_blocklists.is_ad_format_block": "No",
    "advertiser_blocklists.exchange": "VUNGLE,N/A"
}


def auth():
    resp = requests.post(f"{BASE}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={CLIENT_ID}&client_secret={CLIENT_SECRET}")
    resp.raise_for_status()
    return resp.json()["access_token"]


def run_query(token, fields, limit=1):
    resp = requests.post(f"{BASE}/api/4.0/queries/run/json",
        headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
        json={
            "model": "blocklist",
            "view": "advertiser_blocklists",
            "fields": fields,
            "filters": BASE_FILTERS,
            "limit": str(limit)
        }, timeout=120)
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    token = auth()
    print("✓ Authenticated\n")

    # Total row count (all rows including fanout)
    print("Fetching total row count...")
    data = run_query(token, fields=["advertiser_blocklists.count"])
    print(f"  Total rows (incl. fanout): {data[0].get('advertiser_blocklists.count', 'N/A')}")

    # Count by applies_to breakdown
    print("\nFetching count by block level (applies_to)...")
    data2 = run_query(token,
        fields=["advertiser_blocklists.applies_to", "advertiser_blocklists.count"],
        limit=50
    )
    df = pd.DataFrame(data2)
    df.columns = ["applies_to", "count"]
    df["count"] = pd.to_numeric(df["count"], errors="coerce")
    print(df.sort_values("count", ascending=False).to_string(index=False))
    print(f"\nTotal: {df['count'].sum():,}")
