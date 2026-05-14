"""
Low Share of Wallet Analysis — Part 1: Base Spend Data

Fetches:
  A. Demand side spend per source app (90d + 30d) from accelerate_analytics
  B. Supply side app metadata from dmx_reports/publisher_report
  C. Joins on market_id, fills "-" where no match

Filters:
  - vungle_publishers.has_vungle_sdk = Yes
  - Total spend >= $90,000 over 90 complete days (~$1,000/day)
  - 30d data fetched for the same apps identified in 90d (no re-filter)

Output folder: output/low_sov_analysis/
  - raw_demand_90d.csv       — raw demand spend per app (90d)
  - raw_demand_30d.csv       — raw demand spend per app (30d, same app list)
  - raw_supply_metadata.csv  — app name, account, AM from supply side
  - p1_base_spend.csv        — final joined output for Part 1
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
os.makedirs(OUTPUT_DIR, exist_ok=True)

SPEND_THRESHOLD_90D = 90000   # $1,000/day × 90 days
DAYS_90 = 90
DAYS_30 = 30

# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


# ── Auth ──────────────────────────────────────────────────────────────────────

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


def run_query(token, model, view, fields, filters, sorts=None, limit=50000):
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


# ── Step A: Demand side — 90d ─────────────────────────────────────────────────

def fetch_demand_90d(token):
    cache = f"{OUTPUT_DIR}/raw_demand_90d.csv"
    if os.path.exists(cache):
        log(f"Loading 90d demand data from cache: {cache}")
        return pd.read_csv(cache)

    log("Fetching 90d demand spend (accelerate_spot)...", "STEP")
    log("This may take a while — demand side data is large, please wait...")

    data = run_query(
        token,
        model="accelerate_analytics",
        view="accelerate_spot",
        fields=[
            "revenue_summary.source_app_app_store_id",
            "revenue_summary.vungle_or_non_vungle",
            "revenue_summary.revenue"
        ],
        filters={
            "revenue_summary.event_date": f"{DAYS_90} days ago for {DAYS_90} days",
            "vungle_publishers.has_vungle_sdk": "Yes"
        },
        sorts=["revenue_summary.revenue desc"],
        limit=100000
    )

    log(f"Raw rows returned: {len(data)}")
    df = pd.DataFrame(data)
    df.columns = ["market_id", "vungle_or_non_vungle", "revenue"]
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0)
    df = df[df["market_id"].notna() & (df["market_id"] != "")].copy()

    df.to_csv(cache, index=False)
    log(f"Saved raw 90d demand data → {cache}")
    return df


# ── Step A2: Filter to qualifying apps (>= $90k over 90d) ────────────────────

def get_qualifying_apps(df_90d):
    log("Calculating total spend per app and filtering ≥ $90,000 over 90d...", "STEP")

    total_per_app = df_90d.groupby("market_id")["revenue"].sum().reset_index()
    total_per_app.columns = ["market_id", "total_spend_90d"]

    qualifying = total_per_app[total_per_app["total_spend_90d"] >= SPEND_THRESHOLD_90D]["market_id"].tolist()
    log(f"Apps with total spend ≥ ${SPEND_THRESHOLD_90D:,} over 90d: {len(qualifying)}")
    return set(qualifying)


# ── Step A3: Aggregate 90d spend per app (VX vs Non-VX) ──────────────────────

def aggregate_90d(df_90d, qualifying_apps):
    log("Aggregating 90d VX vs Non-VX spend per app...", "STEP")

    df = df_90d[df_90d["market_id"].isin(qualifying_apps)].copy()

    pivot = df.pivot_table(
        index="market_id",
        columns="vungle_or_non_vungle",
        values="revenue",
        aggfunc="sum"
    ).reset_index().fillna(0)

    pivot.columns.name = None
    col_map = {}
    for col in pivot.columns:
        if col == "market_id":
            col_map[col] = "market_id"
        elif "vungle" in col.lower() and "non" not in col.lower():
            col_map[col] = "vx_spend_90d"
        elif "non" in col.lower():
            col_map[col] = "non_vx_spend_90d"
        else:
            col_map[col] = col.lower().replace(" ", "_") + "_90d"
    pivot = pivot.rename(columns=col_map)

    if "vx_spend_90d" not in pivot.columns:
        pivot["vx_spend_90d"] = 0
    if "non_vx_spend_90d" not in pivot.columns:
        pivot["non_vx_spend_90d"] = 0

    pivot["total_spend_90d"] = pivot["vx_spend_90d"] + pivot["non_vx_spend_90d"]
    pivot["vx_spend_pct_90d"] = (
        pivot["vx_spend_90d"] / pivot["total_spend_90d"] * 100
    ).round(2).where(pivot["total_spend_90d"] > 0, 0)

    log(f"90d aggregation complete: {len(pivot)} apps")
    return pivot


# ── Step B: Demand side — 30d (same app list) ────────────────────────────────

def fetch_demand_30d(token, qualifying_apps):
    cache = f"{OUTPUT_DIR}/raw_demand_30d.csv"
    if os.path.exists(cache):
        log(f"Loading 30d demand data from cache: {cache}")
        return pd.read_csv(cache)

    log("Fetching 30d demand spend for qualifying apps (accelerate_spot)...", "STEP")
    log("Fetching 30d data — please wait...")

    id_filter = ",".join(list(qualifying_apps)[:5000])

    data = run_query(
        token,
        model="accelerate_analytics",
        view="accelerate_spot",
        fields=[
            "revenue_summary.source_app_app_store_id",
            "revenue_summary.vungle_or_non_vungle",
            "revenue_summary.revenue"
        ],
        filters={
            "revenue_summary.event_date": f"{DAYS_30} days ago for {DAYS_30} days",
            "vungle_publishers.has_vungle_sdk": "Yes",
            "revenue_summary.source_app_app_store_id": id_filter
        },
        sorts=["revenue_summary.revenue desc"],
        limit=100000
    )

    log(f"Raw rows returned: {len(data)}")
    df = pd.DataFrame(data)
    df.columns = ["market_id", "vungle_or_non_vungle", "revenue"]
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0)
    df = df[df["market_id"].notna() & (df["market_id"] != "")].copy()

    df.to_csv(cache, index=False)
    log(f"Saved raw 30d demand data → {cache}")
    return df


# ── Step B2: Aggregate 30d spend per app ─────────────────────────────────────

def aggregate_30d(df_30d, qualifying_apps):
    log("Aggregating 30d VX vs Non-VX spend per app...", "STEP")

    df = df_30d[df_30d["market_id"].isin(qualifying_apps)].copy()

    pivot = df.pivot_table(
        index="market_id",
        columns="vungle_or_non_vungle",
        values="revenue",
        aggfunc="sum"
    ).reset_index().fillna(0)

    pivot.columns.name = None
    col_map = {}
    for col in pivot.columns:
        if col == "market_id":
            col_map[col] = "market_id"
        elif "vungle" in col.lower() and "non" not in col.lower():
            col_map[col] = "vx_spend_30d"
        elif "non" in col.lower():
            col_map[col] = "non_vx_spend_30d"
        else:
            col_map[col] = col.lower().replace(" ", "_") + "_30d"
    pivot = pivot.rename(columns=col_map)

    if "vx_spend_30d" not in pivot.columns:
        pivot["vx_spend_30d"] = 0
    if "non_vx_spend_30d" not in pivot.columns:
        pivot["non_vx_spend_30d"] = 0

    pivot["total_spend_30d"] = pivot["vx_spend_30d"] + pivot["non_vx_spend_30d"]
    pivot["vx_spend_pct_30d"] = (
        pivot["vx_spend_30d"] / pivot["total_spend_30d"] * 100
    ).round(2).where(pivot["total_spend_30d"] > 0, 0)

    log(f"30d aggregation complete: {len(pivot)} apps")
    return pivot


# ── Step C: Supply metadata (no date filter — dimensions only) ────────────────

def fetch_supply_metadata(token):
    cache = f"{OUTPUT_DIR}/raw_supply_metadata.csv"
    if os.path.exists(cache):
        log(f"Loading supply metadata from cache: {cache}")
        return pd.read_csv(cache)

    log("Fetching supply metadata (publisher_report)...", "STEP")

    data = run_query(
        token,
        model="dmx_reports",
        view="publisher_report",
        fields=[
            "publisher_apps.market_id",
            "publisher_apps.name",
            "publisher_accounts.name",
            "salesforce_accounts_monetize.am_user_name"
        ],
        filters={},
        limit=50000
    )

    log(f"Raw supply rows returned: {len(data)}")
    df = pd.DataFrame(data)
    df.columns = ["market_id", "app_name", "account_name", "am_name"]
    df = df[df["market_id"].notna() & (df["market_id"] != "")].copy()
    df = df.drop_duplicates(subset=["market_id"]).copy()

    df.to_csv(cache, index=False)
    log(f"Saved supply metadata → {cache}")
    return df


# ── Step D: Join and produce Part 1 output ────────────────────────────────────

def build_p1(agg_90d, agg_30d, supply_meta):
    log("Joining demand data with supply metadata...", "STEP")

    df = agg_90d.copy()
    df = df.merge(agg_30d, on="market_id", how="left")

    for col in ["vx_spend_30d", "non_vx_spend_30d", "total_spend_30d", "vx_spend_pct_30d"]:
        if col not in df.columns:
            df[col] = 0
    df[["vx_spend_30d", "non_vx_spend_30d", "total_spend_30d", "vx_spend_pct_30d"]] = \
        df[["vx_spend_30d", "non_vx_spend_30d", "total_spend_30d", "vx_spend_pct_30d"]].fillna(0)

    df = df.merge(supply_meta, on="market_id", how="left")

    for col in ["app_name", "account_name", "am_name"]:
        df[col] = df[col].fillna("-")

    df["from_previous_analysis"] = ""
    df["won_or_lost_rank_30d"] = ""

    cols = [
        "market_id", "app_name", "account_name", "am_name",
        "total_spend_90d", "non_vx_spend_90d", "vx_spend_90d", "vx_spend_pct_90d",
        "from_previous_analysis",
        "total_spend_30d", "non_vx_spend_30d", "vx_spend_30d", "vx_spend_pct_30d",
        "won_or_lost_rank_30d"
    ]
    df = df[[c for c in cols if c in df.columns]]
    df = df.sort_values("total_spend_90d", ascending=False).reset_index(drop=True)

    output_path = f"{OUTPUT_DIR}/p1_base_spend.csv"
    df.to_csv(output_path, index=False)
    log(f"Part 1 complete → {output_path}")
    log(f"  Total qualifying apps: {len(df)}")
    log(f"  Apps with supply metadata: {(df['app_name'] != '-').sum()}")
    log(f"  Apps without supply metadata: {(df['app_name'] == '-').sum()}")
    return df


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    log("═" * 50, "STEP")
    log("LOW SHARE OF WALLET ANALYSIS — PART 1", "STEP")
    log("═" * 50, "STEP")

    token = get_looker_token()

    df_90d_raw = fetch_demand_90d(token)
    qualifying_apps = get_qualifying_apps(df_90d_raw)
    agg_90d = aggregate_90d(df_90d_raw, qualifying_apps)

    df_30d_raw = fetch_demand_30d(token, qualifying_apps)
    agg_30d = aggregate_30d(df_30d_raw, qualifying_apps)

    supply_meta = fetch_supply_metadata(token)

    p1 = build_p1(agg_90d, agg_30d, supply_meta)

    log("═" * 50, "STEP")
    log("PART 1 COMPLETE", "STEP")
    log(f"Output: {OUTPUT_DIR}/p1_base_spend.csv", "STEP")
    log("Next: run low_sov_part2_rankings.py", "STEP")
    log("═" * 50, "STEP")