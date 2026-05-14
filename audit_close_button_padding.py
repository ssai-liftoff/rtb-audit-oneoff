"""
Close Button Padding Audit
Fetches closeButtonPaddingType from RTB API for all high-spend connections.

Output: output/audit_close_button_padding.csv
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
RTB_API_TOKEN = os.getenv("RTB_API_TOKEN")

RTB_API_BASE = "https://pub-ctrl-api.vungle.com/api/v1/rtbconnections"
HEADERS = {
    "authorization": f"Bearer {RTB_API_TOKEN}",
    "vungle-source": "admin",
    "vungle-version": "1",
    "content-type": "application/json"
}

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


# ── Fetch RTB connections from Looker ─────────────────────────────────────────

def fetch_rtb_connections(token):
    print("\nFetching RTB connections from Looker...")

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
                "salesforce_accounts_programmatic.am_user_name",
                "vx_overview.unified_ad_spend"
            ],
            "filters": {
                "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
            },
            "limit": "10000"
        }
    )
    resp.raise_for_status()
    data = resp.json()

    df = pd.DataFrame(data)
    df.columns = [
        "rtb_connection_id", "rtb_connection_name",
        "rtb_account_id", "rtb_account_name",
        "liftoff_owner", "total_spend_7d"
    ]

    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS

    before = len(df)
    df = df[df["daily_spend"] >= SPEND_THRESHOLD].copy()
    df = df[df["rtb_connection_id"].notna() & (df["rtb_connection_id"] != "")].copy()

    print(f"✓ {len(df)} connections with >${SPEND_THRESHOLD}/day (filtered from {before})")
    return df


# ── Fetch close button padding from API ───────────────────────────────────────

def fetch_padding_type(connection_id):
    try:
        resp = requests.get(
            f"{RTB_API_BASE}/{connection_id}",
            headers=HEADERS,
            timeout=10
        )
        if resp.status_code == 401:
            raise ValueError("RTB API token expired — refresh RTB_API_TOKEN in .env")
        resp.raise_for_status()
        return resp.json().get("closeButtonPaddingType", "not_set")
    except ValueError:
        raise
    except Exception as e:
        print(f"    ⚠ Error fetching {connection_id}: {e}")
        return "error"


# ── Main ──────────────────────────────────────────────────────────────────────

def build_audit():
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")
    if not RTB_API_TOKEN:
        raise ValueError("Missing RTB_API_TOKEN in .env")

    token = get_looker_token()
    df = fetch_rtb_connections(token)

    print(f"\nFetching close button padding for {len(df)} connections...")

    padding_types = []
    for i, row in df.iterrows():
        connection_id = str(row["rtb_connection_id"])
        print(f"  [{i+1}/{len(df)}] {row['rtb_connection_name']} ({connection_id})")
        padding = fetch_padding_type(connection_id)
        padding_types.append(padding)
        time.sleep(0.1)

    df["close_button_padding_type"] = padding_types

    # Final output columns
    output = df[[
        "rtb_connection_id",
        "rtb_connection_name",
        "rtb_account_name",
        "liftoff_owner",
        "daily_spend",
        "close_button_padding_type"
    ]].copy()

    output = output.sort_values("daily_spend", ascending=False).reset_index(drop=True)

    output_path = "output/audit_close_button_padding.csv"
    output.to_csv(output_path, index=False)

    print(f"\n{'='*50}")
    print(f"Audit complete!")
    print(f"  Total connections: {len(output)}")
    print(f"\nPadding type breakdown:")
    print(output["close_button_padding_type"].value_counts().to_string())
    print(f"\nSaved to: {output_path}")
    print(f"{'='*50}")


if __name__ == "__main__":
    build_audit()