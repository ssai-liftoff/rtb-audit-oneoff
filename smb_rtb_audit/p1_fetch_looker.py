"""
SMB RTB App Blocklist Audit — Part 1: Fetch Looker Data

Fetches:
  A. RTB accounts + connections with spend > $1k/day over last 14 days
     from vx_analytics/vx_overview
  B. App × RTB connection spend matrix for the SMB app list
     from vx_analytics/vx_overview using publisher_apps.id

Inputs:
  - smb_rtb_audit/smb_apps.csv   (required column: app_id)

Outputs:
  - output/smb_rtb_audit/p1_rtb_accounts_connections.csv
  - output/smb_rtb_audit/p1_app_connection_spend.csv
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

OUTPUT_DIR = "output/smb_rtb_audit"
os.makedirs(OUTPUT_DIR, exist_ok=True)

LOOKBACK_DAYS = 14
SPEND_THRESHOLD_DAILY = 1000


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


def run_query(token, model, view, fields, filters, sorts=None, limit=100000):
    payload = {
        "model": model,
        "view": view,
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
    if not resp.ok:
        log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
        resp.raise_for_status()
    return resp.json()


# ── Part A: RTB accounts + connections ───────────────────────────────────────

def fetch_rtb_accounts_connections(token):
    cache = f"{OUTPUT_DIR}/p1_rtb_accounts_connections.csv"
    if os.path.exists(cache):
        log(f"Loading RTB accounts/connections from cache: {cache}")
        return pd.read_csv(cache)

    log(f"Fetching RTB accounts + connections spend ({LOOKBACK_DAYS}d) from vx_overview...", "STEP")
    log("Please wait...")

    data = run_query(
        token,
        model="vx_analytics",
        view="vx_overview",
        fields=[
            "rtb_accounts.id",
            "rtb_accounts.name",
            "rtb_accounts.contact_name",
            "rtb_connections.id",
            "rtb_connections.name",
            "vx_overview.unified_ad_spend"
        ],
        filters={
            "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
        },
        sorts=["vx_overview.unified_ad_spend desc"],
        limit=100000
    )

    log(f"Raw rows returned: {len(data)}")
    df = pd.DataFrame(data)
    df.columns = [
        "rtb_account_id", "rtb_account_name", "rtb_contact_name",
        "rtb_connection_id", "rtb_connection_name", "total_spend_14d"
    ]
    df["total_spend_14d"] = pd.to_numeric(df["total_spend_14d"], errors="coerce").fillna(0)
    df["daily_spend"] = (df["total_spend_14d"] / LOOKBACK_DAYS).round(2)
    df = df[df["rtb_account_id"].notna() & (df["rtb_account_id"] != "")].copy()
    df = df[df["rtb_connection_id"].notna() & (df["rtb_connection_id"] != "")].copy()

    # Keep accounts that have at least one connection spending >= threshold
    qualifying_account_ids = (
        df[df["daily_spend"] >= SPEND_THRESHOLD_DAILY]["rtb_account_id"].unique()
    )
    before = df["rtb_account_id"].nunique()
    df = df[df["rtb_account_id"].isin(qualifying_account_ids)].copy()

    log(f"RTB accounts with ≥ ${SPEND_THRESHOLD_DAILY}/day: {len(qualifying_account_ids)} (from {before})")
    log(f"Total connections under those accounts: {df['rtb_connection_id'].nunique()}")

    df.to_csv(cache, index=False)
    log(f"Saved → {cache}")
    return df


# ── Part B: App × RTB connection spend ───────────────────────────────────────

def fetch_app_connection_spend(token, smb_app_ids):
    cache = f"{OUTPUT_DIR}/p1_app_connection_spend.csv"
    if os.path.exists(cache):
        log(f"Loading app × connection spend from cache: {cache}")
        return pd.read_csv(cache)

    log(f"Fetching app × RTB connection spend for {len(smb_app_ids)} SMB apps ({LOOKBACK_DAYS}d)...", "STEP")
    log("Please wait, this may take a while...")

    id_filter = ",".join([str(x) for x in smb_app_ids[:5000]])

    data = run_query(
        token,
        model="vx_analytics",
        view="vx_overview",
        fields=[
            "publisher_apps.id",
            "rtb_connections.id",
            "rtb_connections.name",
            "vx_overview.unified_ad_spend"
        ],
        filters={
            "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
            "publisher_apps.id": id_filter
        },
        sorts=["vx_overview.unified_ad_spend desc"],
        limit=100000
    )

    log(f"Raw rows returned: {len(data)}")
    df = pd.DataFrame(data)
    df.columns = ["app_id", "rtb_connection_id", "rtb_connection_name", "spend_14d"]
    df["spend_14d"] = pd.to_numeric(df["spend_14d"], errors="coerce").fillna(0)
    df["daily_spend"] = (df["spend_14d"] / LOOKBACK_DAYS).round(4)
    df = df[df["app_id"].notna() & (df["app_id"] != "")].copy()
    df = df[df["rtb_connection_id"].notna() & (df["rtb_connection_id"] != "")].copy()

    df.to_csv(cache, index=False)
    log(f"Saved → {cache}")
    log(f"  Unique apps with any spend: {df['app_id'].nunique()} / {len(smb_app_ids)}")
    log(f"  Unique connections seen: {df['rtb_connection_id'].nunique()}")
    return df


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    smb_path = "smb_rtb_audit/smb_apps.csv"
    if not os.path.exists(smb_path):
        raise FileNotFoundError(
            f"SMB app list not found at {smb_path}\n"
            "Create a CSV with at least an 'app_id' column (publisher_apps.id values)"
        )

    smb_df = pd.read_csv(smb_path)
    smb_app_ids = smb_df["app_id"].dropna().astype(str).tolist()[:500]
    log(f"Loaded {len(smb_app_ids)} SMB apps from {smb_path} (top 500 by spend)")

    log("═" * 55, "STEP")
    log("SMB RTB AUDIT — PART 1: FETCH LOOKER DATA", "STEP")
    log("═" * 55, "STEP")

    token = get_looker_token()

    rtb_df = fetch_rtb_accounts_connections(token)
    app_conn_df = fetch_app_connection_spend(token, smb_app_ids)

    log("═" * 55, "STEP")
    log("PART 1 COMPLETE", "STEP")
    log(f"  RTB accounts fetched:        {rtb_df['rtb_account_id'].nunique()}", "STEP")
    log(f"  RTB connections fetched:     {rtb_df['rtb_connection_id'].nunique()}", "STEP")
    log(f"  App × connection rows:       {len(app_conn_df)}", "STEP")
    log("Next: run p2_filter_rtb_accounts.py", "STEP")
    log("═" * 55, "STEP")
