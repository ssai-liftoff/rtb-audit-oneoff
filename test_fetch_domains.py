"""
Test: fetch advertiser domains from vx_overview.
Run from project root: python3 test_fetch_domains.py
"""
import csv
import json
import os
import requests
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL    = os.environ["LOOKER_BASE_URL"]
LOOKER_CLIENT_ID   = os.environ["LOOKER_CLIENT_ID"]
LOOKER_CLIENT_SECRET = os.environ["LOOKER_CLIENT_SECRET"]

LOOKBACK_DAYS = 7
OUT_FILE = "output/test_domains_raw.csv"
os.makedirs("output", exist_ok=True)


def get_token():
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}"
    )
    resp.raise_for_status()
    print("✓ Authenticated")
    return resp.json()["access_token"]


def run_query(token, fields, filters, limit=5000):
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
        json={
            "model": "vx_analytics",
            "view": "vx_overview",
            "fields": fields,
            "filters": filters,
            "sorts": ["vx_overview.unified_ad_spend desc"],
            "limit": str(limit),
        }
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    token = get_token()

    print("Fetching domains from vx_overview...")
    fields = [
        "vx_overview.adomain",
        "vx_overview.content_category_code",
        "vx_overview.unified_ad_spend",
    ]
    filters = {
        "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
    }

    rows = run_query(token, fields, filters, limit=5000)
    print(f"✓ {len(rows)} rows returned")

    if not rows:
        print("No rows — check filters or Looker credentials.")
    else:
        with open(OUT_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys(), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        print(f"✓ Saved → {OUT_FILE}")
        print("\nFirst 5 rows:")
        for r in rows[:5]:
            print(json.dumps(r, indent=2, default=str))
