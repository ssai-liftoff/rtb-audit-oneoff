"""
RTB Platform Parity Audit — Part 1: Fetch Top RTB Connections by Spend

Pulls RTB connection spend from Looker (vx_overview) and keeps the top N
connections by average daily spend over the last 7 days.

Output: output/rtb_platform_parity_audit/p1_top_rtbs.csv
"""

import os
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

OUTPUT_DIR = "output/rtb_platform_parity_audit"
LOOKBACK_DAYS = 7
TOP_N = 25

os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def get_token():
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}",
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    cache = f"{OUTPUT_DIR}/p1_top_rtbs.csv"
    if os.path.exists(cache):
        log(f"Cache found — loading {cache}")
        df = pd.read_csv(cache)
        log(f"  {len(df):,} RTB connections loaded")
    else:
        log("═" * 60, "STEP")
        log("RTB PLATFORM PARITY — PART 1: TOP RTB CONNECTIONS", "STEP")
        log("═" * 60, "STEP")

        token = get_token()
        log("Authenticated with Looker")

        resp = requests.post(
            f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
            headers=auth_headers(token),
            json={
                "model": "vx_analytics",
                "view": "vx_overview",
                "fields": [
                    "rtb_connections.id",
                    "rtb_connections.name",
                    "rtb_accounts.id",
                    "rtb_accounts.name",
                    "vx_overview.unified_ad_spend",
                ],
                "filters": {
                    "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
                },
                "sorts": ["vx_overview.unified_ad_spend desc"],
                "limit": "5000",
            },
            timeout=300,
        )
        if not resp.ok:
            log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
            resp.raise_for_status()

        raw = pd.DataFrame(resp.json())
        raw.columns = [
            "rtb_connection_id",
            "rtb_connection_name",
            "rtb_account_id",
            "rtb_account_name",
            "total_spend_7d",
        ]
        raw["total_spend_7d"] = pd.to_numeric(raw["total_spend_7d"], errors="coerce").fillna(0)
        raw["daily_spend"] = (raw["total_spend_7d"] / LOOKBACK_DAYS).round(2)
        raw = raw[
            raw["rtb_connection_id"].notna() & (raw["rtb_connection_id"].astype(str).str.strip() != "")
        ].copy()

        df = (
            raw.sort_values("daily_spend", ascending=False)
            .drop_duplicates(subset=["rtb_connection_id"], keep="first")
            .head(TOP_N)
            .reset_index(drop=True)
        )
        df.to_csv(cache, index=False)
        log(f"Saved top {len(df)} connections → {cache}")

    log("═" * 60, "STEP")
    log(f"Top RTB connections: {len(df)}")
    log(f"Total daily spend:   ${df['daily_spend'].sum():,.0f}")
    for _, row in df.head(5).iterrows():
        log(f"  {row['rtb_connection_name'][:50]:<50} ${row['daily_spend']:,.0f}/day")
    log("Next: run p2_fetch_connection_lists.py", "STEP")
    log("═" * 60, "STEP")
