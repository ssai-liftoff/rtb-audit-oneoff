"""
Low Share of Wallet — Placement Gap Audit

Identifies source app × ad_format × logical_size combos where:
  - Non-VX spend > 0
  - VX spend = 0

Scope: all apps in raw_demand_90d.csv (already fetched with has_vungle_sdk = Yes),
       last 30 days.

Strategy to avoid Looker's vungle_publishers CTE timeout:
  - Load the 54k market_ids from the existing raw_demand_90d.csv cache
    (was fetched with has_vungle_sdk = Yes — no need to re-hit that expensive join)
  - Batch into groups of 5000 and run focused format × logical_size queries
  - Query A: Non-VX spend per batch (11 batches)
  - Query B: VX spend per batch (11 batches)
  - Left anti-join locally → gap rows

Output: output/low_sov_analysis/placement_gap_audit.csv
  Columns: market_id, ad_format, logical_size, non_vx_spend_30d, vx_spend_30d
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

DAYS = 30
BATCH_SIZE = 5000

# Source of truth for has_vungle_sdk = Yes market_ids (already cached from Part 1)
DEMAND_90D_CACHE = f"{OUTPUT_DIR}/raw_demand_90d.csv"

CACHE_NVX_DETAIL = f"{OUTPUT_DIR}/raw_pg_nvx_detail.csv"
CACHE_VX_DETAIL = f"{OUTPUT_DIR}/raw_pg_vx_detail.csv"
OUTPUT_PATH = f"{OUTPUT_DIR}/placement_gap_audit.csv"


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def get_looker_token():
    log("Authenticating with Looker...", "STEP")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}",
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    log("Authenticated")
    return token


def looker_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def run_query(token, fields, filters, limit=500000):
    payload = {
        "model": "accelerate_analytics",
        "view": "accelerate_spot",
        "fields": fields,
        "filters": filters,
        "sorts": ["revenue_summary.revenue desc"],
        "limit": str(limit),
    }
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json=payload,
    )
    if not resp.ok:
        log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
        resp.raise_for_status()
    return resp.json()


def load_cache(path):
    df = pd.read_csv(path, dtype=str).fillna("")
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0)
    return df


# ── Steps A & B: Format × logical_size detail for scoped market_ids ──────────

def fetch_detail_batched(token, market_ids, vungle_value, label, cache_path):
    if os.path.exists(cache_path):
        log(f"Cache hit: {cache_path}")
        return load_cache(cache_path)

    batches = [market_ids[i:i + BATCH_SIZE] for i in range(0, len(market_ids), BATCH_SIZE)]
    log(f"Step {label}: Fetching format × logical_size detail ({len(market_ids):,} apps, "
        f"{len(batches)} batch(es))...", "STEP")

    all_rows = []
    for i, batch in enumerate(batches, 1):
        log(f"  Batch {i}/{len(batches)}: {len(batch)} apps...")
        data = run_query(
            token,
            fields=[
                "revenue_summary.source_app_app_store_id",
                "revenue_summary.ad_format",
                "revenue_summary.logical_size",
                "revenue_summary.revenue",
            ],
            filters={
                "revenue_summary.event_date": f"{DAYS} days ago for {DAYS} days",
                "revenue_summary.vungle_or_non_vungle": vungle_value,
                "revenue_summary.source_app_app_store_id": ",".join(batch),
            },
        )
        log(f"    {len(data)} rows")
        all_rows.extend(data)

    log(f"  Total rows across all batches: {len(all_rows):,}")
    df = pd.DataFrame(all_rows)
    df.columns = ["market_id", "ad_format", "logical_size", "revenue"]
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0)
    for col in ["market_id", "ad_format", "logical_size"]:
        df[col] = df[col].fillna("").astype(str).str.strip()

    df.to_csv(cache_path, index=False)
    log(f"  Saved → {cache_path}")
    return df


# ── Build audit ───────────────────────────────────────────────────────────────

def clean_and_agg(df):
    dims = ["market_id", "ad_format", "logical_size"]
    df = df[(df["market_id"] != "") & (df["ad_format"] != "") & (df["logical_size"] != "")].copy()
    df["ad_format"] = df["ad_format"].str.lower()
    df["logical_size"] = df["logical_size"].str.lower()
    return df.groupby(dims)["revenue"].sum().reset_index()


def build_audit(df_nvx, df_vx):
    log("Building placement gap audit...", "STEP")

    nvx = clean_and_agg(df_nvx)
    nvx = nvx[nvx["revenue"] > 0].rename(columns={"revenue": "non_vx_spend_30d"})

    vx = clean_and_agg(df_vx)
    vx = vx[vx["revenue"] > 0]

    dims = ["market_id", "ad_format", "logical_size"]
    vx["_has_vx"] = True

    # Left anti-join: Non-VX combos with no matching VX combo
    gap = nvx.merge(vx[dims + ["_has_vx"]], on=dims, how="left")
    gap = gap[gap["_has_vx"].isna()].drop(columns=["_has_vx"]).copy()
    gap["vx_spend_30d"] = 0

    gap = gap.sort_values(["market_id", "non_vx_spend_30d"], ascending=[True, False])
    gap = gap[["market_id", "ad_format", "logical_size", "non_vx_spend_30d", "vx_spend_30d"]]
    gap = gap.reset_index(drop=True)

    log(f"  Non-VX > 0 combos: {len(nvx):,}")
    log(f"  VX > 0 combos: {len(vx):,}")
    log(f"  Gap combos (Non-VX > 0, VX = 0): {len(gap):,}")
    log(f"  Source apps with at least one gap: {gap['market_id'].nunique():,}")
    return gap


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    log("═" * 55, "STEP")
    log("PLACEMENT GAP AUDIT", "STEP")
    log("═" * 55, "STEP")

    # Load market_ids from the existing Part 1 cache (already has_vungle_sdk = Yes)
    if not os.path.exists(DEMAND_90D_CACHE):
        raise FileNotFoundError(
            f"Missing {DEMAND_90D_CACHE} — run low_sov_part1_base_spend.py first"
        )
    log(f"Loading market_ids from {DEMAND_90D_CACHE}...", "STEP")
    df_demand = pd.read_csv(DEMAND_90D_CACHE, dtype=str).fillna("")
    market_ids = df_demand["market_id"].drop_duplicates().tolist()
    market_ids = [m for m in market_ids if m]
    log(f"  {len(market_ids):,} market_ids loaded (has_vungle_sdk = Yes)")

    needs_fetch = not os.path.exists(CACHE_NVX_DETAIL) or not os.path.exists(CACHE_VX_DETAIL)
    token = get_looker_token() if needs_fetch else None

    # Batched detail queries — no has_vungle_sdk filter needed (ID list guarantees it)
    df_nvx = fetch_detail_batched(token, market_ids, "Non-Vungle", "A (Non-VX detail)", CACHE_NVX_DETAIL)
    df_vx = fetch_detail_batched(token, market_ids, "Vungle", "B (VX detail)", CACHE_VX_DETAIL)

    # Build and save
    audit = build_audit(df_nvx, df_vx)
    audit.to_csv(OUTPUT_PATH, index=False)

    log("═" * 55, "STEP")
    log("DONE", "STEP")
    log(f"Output: {OUTPUT_PATH}")
    log(f"  {len(audit):,} rows across {audit['market_id'].nunique():,} apps")
    log("═" * 55, "STEP")
