"""
SMB Blocks Audit — Part 2: Fetch Network-Wide Reference Spend

Fetches four reference datasets from vx_analytics/vx_overview (last 14 days):
  A. Total network spend        — single denominator for all % calculations
  B. Spend by IAB category code — maps to publisher_accounts/apps blocked_iab_categories
  C. Spend by RTB account       — maps to publisher_accounts.blocked_rtb_accounts
  D. Spend by RTB connection    — maps to publisher_accounts.blocked_rtb_connections

Uplift formula used in p3:
  pct_of_network   = entity_daily_spend / total_network_daily_spend
  estimated_uplift = pct_of_network × publisher_entity_daily_spend

All outputs include pct_of_network pre-computed so p3 can join and multiply directly.

Source:  vx_analytics / vx_overview
Outputs:
  output/smb_blocks_audit/p2_network_total.csv
  output/smb_blocks_audit/p2_network_iab.csv
  output/smb_blocks_audit/p2_network_rtb_accounts.csv
  output/smb_blocks_audit/p2_network_rtb_connections.csv
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


def run_query(token, fields, filters, sorts=None, limit=50000):
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

    log("═" * 55, "STEP")
    log("SMB BLOCKS AUDIT — PART 2: NETWORK REFERENCE SPEND", "STEP")
    log("═" * 55, "STEP")

    token = get_token()
    log("Authenticated with Looker")

    date_filter = f"{LOOKBACK} days ago for {LOOKBACK} days"

    # ── A. Total network spend ────────────────────────────────────────────────

    total_cache = f"{OUTPUT_DIR}/p2_network_total.csv"
    if os.path.exists(total_cache):
        log(f"Cache found — {total_cache}")
        total_df = pd.read_csv(total_cache)
    else:
        log("Fetching total network spend (all publishers, all demand)...", "STEP")
        raw = run_query(
            token,
            fields=["vx_overview.unified_ad_spend"],
            filters={"vx_overview.event_date": date_filter},
            limit=1,
        )
        total_spend_14d = pd.to_numeric(raw[0]["vx_overview.unified_ad_spend"], errors="coerce")
        total_df = pd.DataFrame([{
            "total_spend_14d":   round(total_spend_14d, 2),
            "total_daily_spend": round(total_spend_14d / LOOKBACK, 4),
        }])
        total_df.to_csv(total_cache, index=False)
        log(f"  Total 14d network spend: ${total_spend_14d:,.0f}")
        log(f"  Saved → {total_cache}")

    total_daily_spend = float(total_df["total_daily_spend"].iloc[0])
    log(f"Total network daily spend: ${total_daily_spend:,.0f}")

    # ── B. IAB category spend ─────────────────────────────────────────────────

    iab_cache = f"{OUTPUT_DIR}/p2_network_iab.csv"
    if os.path.exists(iab_cache):
        log(f"Cache found — {iab_cache}")
        iab_df = pd.read_csv(iab_cache)
    else:
        log("Fetching network spend by IAB content category...", "STEP")
        raw = run_query(
            token,
            fields=[
                "vx_overview.content_category_code",
                "vx_overview.unified_ad_spend",
            ],
            filters={"vx_overview.event_date": date_filter},
            sorts=["vx_overview.unified_ad_spend desc"],
            limit=50000,
        )
        log(f"  Rows returned: {len(raw):,}")
        iab_df = pd.DataFrame(raw)
        iab_df.columns = ["iab_category_code", "spend_14d"]
        iab_df["spend_14d"] = pd.to_numeric(iab_df["spend_14d"], errors="coerce").fillna(0)
        iab_df = iab_df[
            iab_df["iab_category_code"].notna()
            & (iab_df["iab_category_code"].astype(str).str.strip() != "")
        ].copy()
        iab_df["daily_spend"]     = (iab_df["spend_14d"] / LOOKBACK).round(4)
        iab_df["pct_of_network"]  = (iab_df["daily_spend"] / total_daily_spend).round(8)
        iab_df = iab_df.sort_values("daily_spend", ascending=False).reset_index(drop=True)
        iab_df.to_csv(iab_cache, index=False)
        log(f"  {len(iab_df):,} IAB categories | saved → {iab_cache}")

    log(f"IAB categories with spend: {len(iab_df):,}")

    # ── C. RTB account spend ──────────────────────────────────────────────────

    rtb_acc_cache = f"{OUTPUT_DIR}/p2_network_rtb_accounts.csv"
    if os.path.exists(rtb_acc_cache):
        log(f"Cache found — {rtb_acc_cache}")
        rtb_acc_df = pd.read_csv(rtb_acc_cache)
    else:
        log("Fetching network spend by RTB account...", "STEP")
        raw = run_query(
            token,
            fields=[
                "rtb_accounts.id",
                "rtb_accounts.name",
                "vx_overview.unified_ad_spend",
            ],
            filters={"vx_overview.event_date": date_filter},
            sorts=["vx_overview.unified_ad_spend desc"],
            limit=50000,
        )
        log(f"  Rows returned: {len(raw):,}")
        rtb_acc_df = pd.DataFrame(raw)
        rtb_acc_df.columns = ["rtb_account_id", "rtb_account_name", "spend_14d"]
        rtb_acc_df["spend_14d"] = pd.to_numeric(rtb_acc_df["spend_14d"], errors="coerce").fillna(0)
        rtb_acc_df = rtb_acc_df[
            rtb_acc_df["rtb_account_id"].notna()
            & (rtb_acc_df["rtb_account_id"].astype(str).str.strip() != "")
        ].copy()
        rtb_acc_df["rtb_account_id"] = rtb_acc_df["rtb_account_id"].astype(str).str.strip()
        rtb_acc_df["daily_spend"]    = (rtb_acc_df["spend_14d"] / LOOKBACK).round(4)
        rtb_acc_df["pct_of_network"] = (rtb_acc_df["daily_spend"] / total_daily_spend).round(8)
        rtb_acc_df = rtb_acc_df.sort_values("daily_spend", ascending=False).reset_index(drop=True)
        rtb_acc_df.to_csv(rtb_acc_cache, index=False)
        log(f"  {len(rtb_acc_df):,} RTB accounts | saved → {rtb_acc_cache}")

    log(f"RTB accounts with spend: {len(rtb_acc_df):,}")

    # ── D. RTB connection spend ───────────────────────────────────────────────

    rtb_conn_cache = f"{OUTPUT_DIR}/p2_network_rtb_connections.csv"
    if os.path.exists(rtb_conn_cache):
        log(f"Cache found — {rtb_conn_cache}")
        rtb_conn_df = pd.read_csv(rtb_conn_cache)
    else:
        log("Fetching network spend by RTB connection...", "STEP")
        raw = run_query(
            token,
            fields=[
                "rtb_connections.id",
                "rtb_connections.name",
                "vx_overview.unified_ad_spend",
            ],
            filters={"vx_overview.event_date": date_filter},
            sorts=["vx_overview.unified_ad_spend desc"],
            limit=50000,
        )
        log(f"  Rows returned: {len(raw):,}")
        rtb_conn_df = pd.DataFrame(raw)
        rtb_conn_df.columns = ["rtb_connection_id", "rtb_connection_name", "spend_14d"]
        rtb_conn_df["spend_14d"] = pd.to_numeric(rtb_conn_df["spend_14d"], errors="coerce").fillna(0)
        rtb_conn_df = rtb_conn_df[
            rtb_conn_df["rtb_connection_id"].notna()
            & (rtb_conn_df["rtb_connection_id"].astype(str).str.strip() != "")
        ].copy()
        rtb_conn_df["rtb_connection_id"] = rtb_conn_df["rtb_connection_id"].astype(str).str.strip()
        rtb_conn_df["daily_spend"]       = (rtb_conn_df["spend_14d"] / LOOKBACK).round(4)
        rtb_conn_df["pct_of_network"]    = (rtb_conn_df["daily_spend"] / total_daily_spend).round(8)
        rtb_conn_df = rtb_conn_df.sort_values("daily_spend", ascending=False).reset_index(drop=True)
        rtb_conn_df.to_csv(rtb_conn_cache, index=False)
        log(f"  {len(rtb_conn_df):,} RTB connections | saved → {rtb_conn_cache}")

    log(f"RTB connections with spend: {len(rtb_conn_df):,}")

    log("═" * 55, "STEP")
    log("PART 2 COMPLETE", "STEP")
    log(f"  Total network daily spend:  ${total_daily_spend:,.0f}", "STEP")
    log(f"  IAB categories:             {len(iab_df):,}", "STEP")
    log(f"  RTB accounts:               {len(rtb_acc_df):,}", "STEP")
    log(f"  RTB connections:            {len(rtb_conn_df):,}", "STEP")
    log("Next: run p3_build_audit.py", "STEP")
    log("═" * 55, "STEP")
