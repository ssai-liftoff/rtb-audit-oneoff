"""
RTB Platform Parity Audit — Part 3: Fetch App Metadata + Publisher Portfolios

1. Collects all app IDs referenced in Part 2 allow/deny lists.
2. Fetches app metadata (name, market_id, account) from Looker in batches.
3. Fetches the full app portfolio for every publisher account involved.

Input:  output/rtb_platform_parity_audit/p2_connection_lists.csv
Output: output/rtb_platform_parity_audit/p3_listed_apps.csv
        output/rtb_platform_parity_audit/p3_account_portfolios.csv
"""

import json
import os
import re
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
BATCH_SIZE = 5000

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


def infer_platform(market_id, app_name=""):
    mid = str(market_id or "").strip()
    if re.fullmatch(r"\d+", mid):
        return "ios"
    if "." in mid:
        return "android"
    name = str(app_name or "").lower()
    if re.search(r"\bios\b|iphone|app store", name):
        return "ios"
    if re.search(r"\bandroid\b|\bgp\b|google play", name):
        return "android"
    return "unknown"


def parse_id_list(raw):
    try:
        return [str(x).strip() for x in json.loads(raw or "[]") if str(x).strip()]
    except Exception:
        return []


def collect_listed_app_ids(p2_df):
    app_ids = set()
    for _, row in p2_df.iterrows():
        if row.get("list_type") == "allow":
            app_ids.update(parse_id_list(row.get("allowed_app_ids")))
        elif row.get("list_type") == "deny":
            app_ids.update(parse_id_list(row.get("denied_app_ids")))
    return sorted(app_ids)


def fetch_apps_by_id(token, app_ids):
    rows = []
    batches = [app_ids[i : i + BATCH_SIZE] for i in range(0, len(app_ids), BATCH_SIZE)]
    for i, batch in enumerate(batches, 1):
        log(f"  App metadata batch {i}/{len(batches)} ({len(batch)} ids)...")
        resp = requests.post(
            f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
            headers=auth_headers(token),
            json={
                "model": "dmx_reports",
                "view": "publisher_report",
                "fields": [
                    "publisher_apps.id",
                    "publisher_apps.name",
                    "publisher_apps.market_id",
                    "publisher_accounts.id",
                    "publisher_accounts.name",
                    "publisher_report.unified_ad_spend",
                ],
                "filters": {
                    "publisher_apps.id": ",".join(batch),
                    "publisher_report.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
                },
                "limit": str(len(batch) + 100),
            },
            timeout=300,
        )
        if not resp.ok:
            log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
            resp.raise_for_status()
        rows.extend(resp.json())
    return rows


def fetch_apps_by_account(token, account_ids):
    rows = []
    batches = [account_ids[i : i + BATCH_SIZE] for i in range(0, len(account_ids), BATCH_SIZE)]
    for i, batch in enumerate(batches, 1):
        log(f"  Account portfolio batch {i}/{len(batches)} ({len(batch)} accounts)...")
        resp = requests.post(
            f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
            headers=auth_headers(token),
            json={
                "model": "dmx_reports",
                "view": "publisher_report",
                "fields": [
                    "publisher_apps.id",
                    "publisher_apps.name",
                    "publisher_apps.market_id",
                    "publisher_accounts.id",
                    "publisher_accounts.name",
                    "publisher_report.unified_ad_spend",
                ],
                "filters": {
                    "publisher_accounts.id": ",".join(batch),
                    "publisher_report.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
                },
                "limit": "100000",
            },
            timeout=300,
        )
        if not resp.ok:
            log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
            resp.raise_for_status()
        rows.extend(resp.json())
    return rows


def normalize_apps_df(raw_rows):
    if not raw_rows:
        return pd.DataFrame(
            columns=[
                "app_id",
                "app_name",
                "market_id",
                "account_id",
                "account_name",
                "total_spend_7d",
                "daily_spend",
                "platform",
            ]
        )

    df = pd.DataFrame(raw_rows)
    df.columns = [
        "app_id",
        "app_name",
        "market_id",
        "account_id",
        "account_name",
        "total_spend_7d",
    ]
    for col in ["app_id", "market_id", "account_id"]:
        df[col] = df[col].astype(str).str.strip()
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df = (
        df.groupby(["app_id", "app_name", "market_id", "account_id", "account_name"], dropna=False)
        .agg(total_spend_7d=("total_spend_7d", "sum"))
        .reset_index()
    )
    df["daily_spend"] = (df["total_spend_7d"] / LOOKBACK_DAYS).round(2)
    df["platform"] = df.apply(lambda r: infer_platform(r["market_id"], r["app_name"]), axis=1)
    return df


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    p2_path = f"{OUTPUT_DIR}/p2_connection_lists.csv"
    listed_cache = f"{OUTPUT_DIR}/p3_listed_apps.csv"
    portfolio_cache = f"{OUTPUT_DIR}/p3_account_portfolios.csv"

    if not os.path.exists(p2_path):
        raise FileNotFoundError(f"Not found: {p2_path} — run p2_fetch_connection_lists.py first")

    if os.path.exists(listed_cache) and os.path.exists(portfolio_cache):
        log(f"Cache found — loading {listed_cache} and {portfolio_cache}")
        listed_df = pd.read_csv(listed_cache)
        portfolio_df = pd.read_csv(portfolio_cache)
    else:
        log("═" * 60, "STEP")
        log("RTB PLATFORM PARITY — PART 3: FETCH APP PORTFOLIOS", "STEP")
        log("═" * 60, "STEP")

        p2 = pd.read_csv(p2_path)
        listed_app_ids = collect_listed_app_ids(p2)
        log(f"Unique apps on allow/deny lists: {len(listed_app_ids):,}")

        if not listed_app_ids:
            log("No allow/deny list apps found — nothing to fetch.", "WARN")
            listed_df = normalize_apps_df([])
            portfolio_df = normalize_apps_df([])
        else:
            token = get_token()
            log("Authenticated with Looker")

            log("Fetching metadata for listed apps...")
            listed_raw = fetch_apps_by_id(token, listed_app_ids)
            listed_df = normalize_apps_df(listed_raw)
            missing = set(listed_app_ids) - set(listed_df["app_id"].tolist())
            if missing:
                log(f"  {len(missing):,} listed apps not found in Looker (deleted/inactive)", "WARN")

            account_ids = sorted(listed_df["account_id"].dropna().unique().tolist())
            log(f"Fetching full portfolios for {len(account_ids):,} publisher accounts...")
            portfolio_raw = fetch_apps_by_account(token, account_ids)
            portfolio_df = normalize_apps_df(portfolio_raw)

        listed_df.to_csv(listed_cache, index=False)
        portfolio_df.to_csv(portfolio_cache, index=False)
        log(f"Saved → {listed_cache} ({len(listed_df):,} rows)")
        log(f"Saved → {portfolio_cache} ({len(portfolio_df):,} rows)")

    log("═" * 60, "STEP")
    log(f"Listed apps:        {len(listed_df):,}")
    log(f"Portfolio apps:     {len(portfolio_df):,}")
    log(f"Unique accounts:    {portfolio_df['account_id'].nunique():,}")
    if len(portfolio_df):
        log(f"Platform split:     {portfolio_df['platform'].value_counts().to_dict()}")
    log("Next: run p4_match_siblings.py", "STEP")
    log("═" * 60, "STEP")
