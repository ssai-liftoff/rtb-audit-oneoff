"""
Supply Metadata Refresh
Fetches supply metadata using demand-side qualifying app market IDs as filter.
Uses 90 day date range to maximise match rate.

Reads:  output/low_sov_analysis/raw_demand_90d.csv  (to get market ID list)
Writes: output/low_sov_analysis/raw_supply_metadata.csv  (overwrites cache)
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

OUTPUT_DIR = "output/low_sov_analysis"
DAYS_90 = 90
SPEND_THRESHOLD_90D = 90000

os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def get_looker_token():
    log("Authenticating with Looker API...", "STEP")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}"
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    log("Authenticated successfully")
    return token


def looker_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


if __name__ == "__main__":
    # ── Load qualifying market IDs from demand side output ────────────────────
    demand_cache = f"{OUTPUT_DIR}/raw_demand_90d.csv"
    if not os.path.exists(demand_cache):
        raise FileNotFoundError(f"Demand data not found: {demand_cache} — run Part 1 first")

    log("Loading qualifying market IDs from demand data...", "STEP")
    df_demand = pd.read_csv(demand_cache)
    df_demand["revenue"] = pd.to_numeric(df_demand["revenue"], errors="coerce").fillna(0)

    # Get qualifying apps
    total_per_app = df_demand.groupby("market_id")["revenue"].sum().reset_index()
    qualifying_ids = total_per_app[
        total_per_app["revenue"] >= SPEND_THRESHOLD_90D
    ]["market_id"].tolist()

    log(f"Qualifying market IDs from demand side: {len(qualifying_ids)}")

    # ── Fetch supply metadata filtered to those IDs ───────────────────────────
    token = get_looker_token()

    id_filter = ",".join([str(x) for x in qualifying_ids[:5000]])

    log("Fetching supply metadata with market ID filter + 90d date range...", "STEP")
    log(f"Filtering to {len(qualifying_ids)} market IDs...")

    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model": "dmx_reports",
            "view": "publisher_report",
            "fields": [
                "publisher_apps.market_id",
                "publisher_apps.name",
                "publisher_accounts.name",
                "salesforce_accounts_monetize.am_user_name"
            ],
            "filters": {
                "publisher_apps.market_id": id_filter,
                "publisher_report.event_date": f"{DAYS_90} days ago for {DAYS_90} days"
            },
            "limit": "50000"
        }
    )

    if not resp.ok:
        log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
        resp.raise_for_status()

    data = resp.json()
    log(f"Raw supply rows returned: {len(data)}")

    df = pd.DataFrame(data)
    df.columns = ["market_id", "app_name", "account_name", "am_name"]
    df = df[df["market_id"].notna() & (df["market_id"] != "")].copy()
    df = df.drop_duplicates(subset=["market_id"]).copy()

    # Fill blanks
    for col in ["app_name", "account_name", "am_name"]:
        df[col] = df[col].fillna("-")

    # Save — overwrites existing cache
    out_path = f"{OUTPUT_DIR}/raw_supply_metadata.csv"
    df.to_csv(out_path, index=False)

    log(f"Saved {len(df)} supply metadata rows → {out_path}")
    log(f"Match rate: {len(df)}/{len(qualifying_ids)} = {len(df)/len(qualifying_ids)*100:.1f}%")
    log("Done! Now re-run Part 1 to rebuild p1_base_spend.csv with updated metadata.")