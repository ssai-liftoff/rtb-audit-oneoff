"""
SMB Blocks Audit — Part 1: Fetch SMB Publisher Accounts & Apps with Blocks

Fetches all SMB publisher accounts and apps (is_managed = "No") along with
their current block configurations and 14-day VX spend. Computes:
  - app_daily_spend     = app_spend_14d / 14
  - account_daily_spend = sum of qualifying app daily spends per account
                          (used as the spend base for account-level blocks)

Qualifying filter: app_daily_spend > $1/day (spend_14d > $14)

Block fields fetched (comma-separated strings from Looker):
  publisher_accounts.blocked_iab_categories  — account-level IAB blocks
  publisher_apps.blocked_iab_categories      — app-level IAB blocks
  publisher_accounts.blocked_rtb_accounts    — account-level RTB DSP blocks
  publisher_accounts.blocked_rtb_connections — account-level RTB connection blocks

Each block type lives in its own column, so one row is returned per app
with no deduplication needed.

Source:  vx_analytics / vx_overview
Output:  output/smb_blocks_audit/p1_smb_blocks.csv
"""

import os
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL      = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID     = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

OUTPUT_DIR = "output/smb_blocks_audit"
LOOKBACK   = 14
MIN_DAILY  = 1.0   # $1/day minimum

os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def get_token():
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}"
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def run_query(token, fields, filters, sorts=None, limit=100000):
    payload = {
        "model":   "vx_analytics",
        "view":    "vx_overview",
        "fields":  fields,
        "filters": filters,
        "limit":   str(limit),
    }
    if sorts:
        payload["sorts"] = sorts
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=auth_headers(token),
        json=payload,
        timeout=300,
    )
    if not resp.ok:
        log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
        resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    cache = f"{OUTPUT_DIR}/p1_smb_blocks.csv"
    if os.path.exists(cache):
        log(f"Cache found — loading {cache}")
        df = pd.read_csv(cache)
        log(f"  {len(df):,} app rows loaded")
    else:
        log("═" * 55, "STEP")
        log("SMB BLOCKS AUDIT — PART 1: FETCH SMB BLOCKS", "STEP")
        log("═" * 55, "STEP")

        token = get_token()
        log("Authenticated with Looker")
        log(f"Fetching SMB apps + block configs (last {LOOKBACK}d)...", "STEP")
        log("Please wait...")

        raw = run_query(
            token,
            fields=[
                "publisher_accounts.id",
                "publisher_accounts.name",
                "publisher_apps.id",
                "publisher_apps.name",
                "publisher_accounts.blocked_iab_categories",
                "publisher_apps.blocked_iab_categories",
                "publisher_accounts.blocked_rtb_accounts",
                "publisher_accounts.blocked_rtb_connections",
                "vx_overview.unified_ad_spend",
            ],
            filters={
                "publisher_accounts.is_managed": "No",
                "vx_overview.event_date": f"{LOOKBACK} days ago for {LOOKBACK} days",
            },
            sorts=["vx_overview.unified_ad_spend desc"],
            limit=100000,
        )

        log(f"Raw rows returned: {len(raw):,}")
        if len(raw) >= 100000:
            log("Hit row limit of 100k — results may be truncated!", "WARN")

        raw_df = pd.DataFrame(raw)
        raw_df.columns = [
            "account_id", "account_name",
            "app_id", "app_name",
            "account_blocked_iab", "app_blocked_iab",
            "account_blocked_rtb_accounts", "account_blocked_rtb_connections",
            "spend_14d",
        ]
        raw_df["spend_14d"] = pd.to_numeric(raw_df["spend_14d"], errors="coerce").fillna(0)
        for col in ["account_id", "app_id"]:
            raw_df[col] = raw_df[col].astype(str).str.strip()
        raw_df = raw_df[
            raw_df["app_id"].notna()
            & (raw_df["app_id"] != "")
            & (raw_df["app_id"] != "nan")
        ].copy()
        log(f"Rows returned: {len(raw_df):,}")

        df = raw_df.copy()
        df["app_daily_spend"] = (df["spend_14d"] / LOOKBACK).round(4)

        # Filter: apps with > $1/day spend
        before = len(df)
        df = df[df["app_daily_spend"] > MIN_DAILY].copy()
        log(f"Apps after >${MIN_DAILY}/day filter: {len(df):,} (from {before:,})")

        # Account daily spend = sum of qualifying app daily spends per account
        acct_daily = (
            df.groupby("account_id")["app_daily_spend"]
            .sum()
            .reset_index()
            .rename(columns={"app_daily_spend": "account_daily_spend"})
        )
        df = df.merge(acct_daily, on="account_id", how="left")
        df["account_daily_spend"] = df["account_daily_spend"].round(4)

        df = df.reset_index(drop=True)
        df.to_csv(cache, index=False)
        log(f"Saved → {cache}")

    # ── Summary ──────────────────────────────────────────────────────────────
    def has_blocks(col):
        return df[col].notna() & (df[col].astype(str).str.strip() != "")

    log("═" * 55, "STEP")
    log("PART 1 COMPLETE", "STEP")
    log(f"  Qualifying apps:              {df['app_id'].nunique():,}", "STEP")
    log(f"  Qualifying accounts:          {df['account_id'].nunique():,}", "STEP")
    log(f"  Apps w/ account IAB blocks:   {df[has_blocks('account_blocked_iab')]['app_id'].nunique():,}", "STEP")
    log(f"  Apps w/ app IAB blocks:       {df[has_blocks('app_blocked_iab')]['app_id'].nunique():,}", "STEP")
    log(f"  Accts w/ RTB acct blocks:     {df[has_blocks('account_blocked_rtb_accounts')]['account_id'].nunique():,}", "STEP")
    log(f"  Accts w/ RTB conn blocks:     {df[has_blocks('account_blocked_rtb_connections')]['account_id'].nunique():,}", "STEP")
    log("Next: run p2_fetch_network_spend.py", "STEP")
    log("═" * 55, "STEP")
