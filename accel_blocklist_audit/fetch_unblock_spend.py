"""
Fetch Post-Unblock Spend — Gross & Net Revenue by Day

Reads the list of 1,415 unblocked combinations from unblocked_combos.csv
and fetches daily gross + net revenue from accelerate_spot for the last
15 days including today (exchange = VUNGLE only, revenue > 0 only).

Block level → Looker entity field mapping:
  campaign       → revenue_summary.campaign_id
  customer       → revenue_summary.customer_id
  advertiser_app → revenue_summary.dest_app_id
  global         → no entity filter (all Accelerate spend on that source app)

One query per block level — all source app IDs + entity IDs passed as batch
filters. revenue > 0 filter applied server-side to skip zero-spend rows.
Results are post-filtered to exact (source_app, entity) pairs from the input.

Output: printed to terminal.
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

COMBOS_FILE = "output/accel_blocklist_audit/unblocked_combos.csv"
LOOKBACK    = 15      # days including today
PAGE_SIZE   = 100000


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
    log("Authenticated")
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def run_page(token, fields, filters, offset=0):
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=auth_headers(token),
        json={
            "model":   "accelerate_analytics",
            "view":    "accelerate_spot",
            "fields":  fields,
            "filters": filters,
            "sorts":   ["revenue_summary.event_date desc"],
            "limit":   str(PAGE_SIZE),
            "offset":  str(offset)
        },
        timeout=300
    )
    if not resp.ok:
        log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
        resp.raise_for_status()
    return resp.json()


MAX_ROWS = 2_000_000  # hard cap per query — anything beyond is low-spend fanout


def run_paginated(token, fields, filters, label=""):
    all_rows = []
    offset   = 0
    while True:
        page = run_page(token, fields, filters, offset)
        all_rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        log(f"  {label} paginating… {offset:,} rows so far")
        if offset >= MAX_ROWS:
            log(f"  {label} hit {MAX_ROWS:,} row cap — stopping", "WARN")
            break
    return all_rows


def fetch_level(token, level, source_app_ids, entity_ids, entity_field):
    """Fetch spend for one block level. entity_ids/entity_field are None for global."""
    date_filter = f"{LOOKBACK} days"  # last N days including today

    if level == "global":
        fields = [
            "revenue_summary.source_app_app_store_id",
            "revenue_summary.event_date",
            "revenue_summary.revenue",
            "revenue_summary.total_aovx_nr"
        ]
        filters = {
            "revenue_summary.event_date": date_filter,
            "revenue_summary.exchange":   "VUNGLE",
            "revenue_summary.source_app_app_store_id": ",".join(source_app_ids),
            "revenue_summary.revenue": ">0"
        }
        raw = run_paginated(token, fields, filters, label="global")
        df  = pd.DataFrame(raw)
        if df.empty:
            return pd.DataFrame()
        df.columns = ["source_app_id", "event_date", "gross_revenue", "net_revenue"]
        df["entity_id"]   = "global"
        df["block_level"] = "global"

    else:
        fields = [
            "revenue_summary.source_app_app_store_id",
            entity_field,
            "revenue_summary.event_date",
            "revenue_summary.revenue",
            "revenue_summary.total_aovx_nr"
        ]
        filters = {
            "revenue_summary.event_date": date_filter,
            "revenue_summary.exchange":   "VUNGLE",
            "revenue_summary.source_app_app_store_id": ",".join(source_app_ids),
            entity_field: ",".join(entity_ids),
            "revenue_summary.revenue": ">0"
        }
        raw = run_paginated(token, fields, filters, label=level)
        df  = pd.DataFrame(raw)
        if df.empty:
            return pd.DataFrame()
        df.columns = ["source_app_id", "entity_id", "event_date",
                      "gross_revenue", "net_revenue"]
        df["block_level"] = level

    for col in ["gross_revenue", "net_revenue"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    df["source_app_id"] = df["source_app_id"].astype(str).str.strip()
    df["entity_id"]     = df["entity_id"].astype(str).str.strip()
    return df


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    log("═" * 60, "STEP")
    log(f"POST-UNBLOCK SPEND FETCH — LAST {LOOKBACK} DAYS (incl. today)", "STEP")
    log("═" * 60, "STEP")

    combos = pd.read_csv(COMBOS_FILE, dtype=str)
    combos.columns = ["blocklist_id", "source_app_id", "block_level", "entity_id"]
    combos["source_app_id"] = combos["source_app_id"].str.strip()
    combos["entity_id"]     = combos["entity_id"].fillna("global").str.strip()
    combos["block_level"]   = combos["block_level"].str.strip()
    log(f"Loaded {len(combos):,} unblocked combinations")
    log(f"Block levels: {combos['block_level'].value_counts().to_dict()}")

    LEVEL_FIELD = {
        "campaign":       "revenue_summary.campaign_id",
        "customer":       "revenue_summary.customer_id",
        "advertiser_app": "revenue_summary.dest_app_id",
    }

    token   = get_token()
    results = []

    for level, group in combos.groupby("block_level"):
        log(f"Fetching '{level}' level ({len(group):,} combos)...", "STEP")
        source_apps  = group["source_app_id"].unique().tolist()
        entity_ids   = (group["entity_id"].unique().tolist()
                        if level != "global" else None)
        entity_field = LEVEL_FIELD.get(level)

        df = fetch_level(token, level, source_apps, entity_ids, entity_field)
        if df.empty:
            log(f"  No spend data returned for '{level}'", "WARN")
            continue

        log(f"  Raw rows: {len(df):,}")

        # Post-filter to exact (source_app, entity) pairs from input
        if level != "global":
            valid_pairs = set(zip(group["source_app_id"], group["entity_id"]))
            df = df[df.apply(
                lambda r: (r["source_app_id"], r["entity_id"]) in valid_pairs, axis=1
            )].copy()
        else:
            df = df[df["source_app_id"].isin(set(group["source_app_id"]))].copy()

        log(f"  After filtering to exact combos: {len(df):,} rows")
        results.append(df)

    if not results:
        log("No spend data found for any level.", "WARN")
        exit()

    all_df = pd.concat(results, ignore_index=True)

    # ── DAILY TOTALS ─────────────────────────────────────────────────────────
    print()
    print("═" * 60)
    print(f"GROSS & NET REVENUE BY DAY (last {LOOKBACK} days, all levels)")
    print("═" * 60)
    daily = (
        all_df.groupby("event_date")[["gross_revenue", "net_revenue"]]
        .sum().sort_index(ascending=False).reset_index()
    )
    print(f"{'Date':<14} {'Gross Revenue':>16} {'Net Revenue':>14}")
    print("-" * 46)
    for _, row in daily.iterrows():
        print(f"{row['event_date']:<14} ${row['gross_revenue']:>15,.2f} ${row['net_revenue']:>13,.2f}")
    print("-" * 46)
    print(f"{'TOTAL':<14} ${daily['gross_revenue'].sum():>15,.2f} ${daily['net_revenue'].sum():>13,.2f}")

    # ── BY BLOCK LEVEL ───────────────────────────────────────────────────────
    print()
    print("═" * 60)
    print("BREAKDOWN BY BLOCK LEVEL")
    print("═" * 60)
    level_summary = (
        all_df.groupby("block_level")[["gross_revenue", "net_revenue"]]
        .sum().round(2).reset_index()
        .sort_values("gross_revenue", ascending=False)
    )
    print(f"{'Level':<20} {'Gross Revenue':>16} {'Net Revenue':>14}")
    print("-" * 52)
    for _, row in level_summary.iterrows():
        print(f"{row['block_level']:<20} ${row['gross_revenue']:>15,.2f} ${row['net_revenue']:>13,.2f}")

    # ── TOP 20 COMBOS ────────────────────────────────────────────────────────
    print()
    print("═" * 60)
    print(f"TOP 20 COMBOS BY GROSS REVENUE ({LOOKBACK}-day total)")
    print("═" * 60)
    top = (
        all_df.groupby(["source_app_id", "entity_id", "block_level"])[["gross_revenue", "net_revenue"]]
        .sum().round(2).reset_index()
        .sort_values("gross_revenue", ascending=False)
        .head(20)
    )
    print(f"{'Source App':<35} {'Entity ID':<12} {'Level':<16} {'Gross':>12} {'Net':>12}")
    print("-" * 90)
    for _, row in top.iterrows():
        print(f"{row['source_app_id']:<35} {row['entity_id']:<12} {row['block_level']:<16} "
              f"${row['gross_revenue']:>11,.2f} ${row['net_revenue']:>11,.2f}")
    print("═" * 60)
