"""
Low Share of Wallet Analysis — Part 2: Exchange Rankings

For each qualifying app, fetches spend per exchange and calculates:
  - VX rank (90d and 30d)
  - Exchanges above VX comma-separated (90d and 30d)

Reads:  output/low_sov_analysis/p1_base_spend.csv  (qualifying app list)
Writes: output/low_sov_analysis/raw_exchange_90d.csv
        output/low_sov_analysis/raw_exchange_30d.csv
        output/low_sov_analysis/p2_rankings.csv
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

DAYS_90 = 90
DAYS_30 = 30
VUNGLE_EXCHANGE = "VUNGLE"


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


# ── Fetch exchange spend ───────────────────────────────────────────────────────

def fetch_exchange_spend(token, qualifying_apps, days, label):
    cache = f"{OUTPUT_DIR}/raw_exchange_{label}.csv"
    if os.path.exists(cache):
        log(f"Loading {label} exchange data from cache: {cache}")
        return pd.read_csv(cache)

    log(f"Fetching {label} exchange spend per app (accelerate_spot)...", "STEP")
    log("Please wait...")

    id_filter = ",".join(list(qualifying_apps)[:5000])

    data = run_query(
        token,
        model="accelerate_analytics",
        view="accelerate_spot",
        fields=[
            "revenue_summary.source_app_app_store_id",
            "revenue_summary.exchange",
            "revenue_summary.revenue"
        ],
        filters={
            "revenue_summary.event_date": f"{days} days ago for {days} days",
            "vungle_publishers.has_vungle_sdk": "Yes",
            "revenue_summary.source_app_app_store_id": id_filter
        },
        sorts=["revenue_summary.revenue desc"]
    )

    log(f"Raw rows returned: {len(data)}")
    df = pd.DataFrame(data)
    df.columns = ["market_id", "exchange", "revenue"]
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0)
    df = df[df["market_id"].notna() & (df["market_id"] != "")].copy()
    df = df[df["exchange"].notna() & (df["exchange"] != "")].copy()

    df.to_csv(cache, index=False)
    log(f"Saved {label} exchange data → {cache}")
    return df


# ── Calculate rankings ────────────────────────────────────────────────────────

def calculate_rankings(df_exchange, label):
    log(f"Calculating VX rankings ({label})...", "STEP")

    # Aggregate spend per app × exchange
    agg = df_exchange.groupby(["market_id", "exchange"])["revenue"].sum().reset_index()

    # Rank exchanges within each app by spend descending
    agg["rank"] = agg.groupby("market_id")["revenue"].rank(
        method="dense", ascending=False
    ).astype(int)

    # Get VX rank per app
    vx = agg[agg["exchange"].str.upper() == VUNGLE_EXCHANGE][["market_id", "rank"]].copy()
    vx.columns = ["market_id", f"vx_rank_{label}"]

    # Get exchanges above VX per app
    def exchanges_above(group):
        market_id = group["market_id"].iloc[0]
        vx_row = vx[vx["market_id"] == market_id]
        if vx_row.empty:
            return None
        vx_rank = vx_row[f"vx_rank_{label}"].iloc[0]
        above = group[group["rank"] < vx_rank].sort_values("rank")["exchange"].tolist()
        return ", ".join(above) if above else ""

    exchanges_above_df = agg.groupby("market_id").apply(
        exchanges_above
    ).reset_index()
    exchanges_above_df.columns = ["market_id", f"exchanges_above_vx_{label}"]

    # Merge
    result = vx.merge(exchanges_above_df, on="market_id", how="outer")

    # Fill apps where VX has no spend
    result[f"vx_rank_{label}"] = result[f"vx_rank_{label}"].fillna(-1).astype(int)
    result[f"exchanges_above_vx_{label}"] = result[f"exchanges_above_vx_{label}"].fillna("VX not present")

    log(f"  Apps with VX present: {(result[f'vx_rank_{label}'] > 0).sum()}")
    log(f"  Apps where VX is #1: {(result[f'vx_rank_{label}'] == 1).sum()}")
    log(f"  Apps where VX is #2+: {(result[f'vx_rank_{label}'] > 1).sum()}")
    log(f"  Apps where VX not present: {(result[f'vx_rank_{label}'] == -1).sum()}")

    return result


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    log("═" * 50, "STEP")
    log("LOW SHARE OF WALLET ANALYSIS — PART 2", "STEP")
    log("═" * 50, "STEP")

    # Load qualifying apps from Part 1
    p1_path = f"{OUTPUT_DIR}/p1_base_spend.csv"
    if not os.path.exists(p1_path):
        raise FileNotFoundError(f"Part 1 output not found: {p1_path} — run Part 1 first")

    log("Loading qualifying apps from Part 1...", "STEP")
    p1 = pd.read_csv(p1_path)
    qualifying_apps = set(p1["market_id"].tolist())
    log(f"  {len(qualifying_apps)} qualifying apps loaded")

    token = get_looker_token()

    # Fetch exchange spend
    df_exchange_90d = fetch_exchange_spend(token, qualifying_apps, DAYS_90, "90d")
    df_exchange_30d = fetch_exchange_spend(token, qualifying_apps, DAYS_30, "30d")

    # Calculate rankings
    rankings_90d = calculate_rankings(df_exchange_90d, "90d")
    rankings_30d = calculate_rankings(df_exchange_30d, "30d")

    # Merge rankings into Part 1
    log("Merging rankings into Part 1 output...", "STEP")
    p2 = p1.copy()
    p2 = p2.merge(rankings_90d, on="market_id", how="left")
    p2 = p2.merge(rankings_30d, on="market_id", how="left")

    # Fill missing
    p2["vx_rank_90d"] = p2["vx_rank_90d"].fillna(-1).astype(int)
    p2["vx_rank_30d"] = p2["vx_rank_30d"].fillna(-1).astype(int)
    p2["exchanges_above_vx_90d"] = p2["exchanges_above_vx_90d"].fillna("VX not present")
    p2["exchanges_above_vx_30d"] = p2["exchanges_above_vx_30d"].fillna("VX not present")

    # Calculate won/lost rank
    def won_or_lost(row):
        r90 = row["vx_rank_90d"]
        r30 = row["vx_rank_30d"]
        if r90 <= 0 or r30 <= 0:
            return "-"
        if r30 < r90:
            return "Won"
        elif r30 > r90:
            return "Lost"
        else:
            return "Held"

    p2["won_or_lost_rank_30d"] = p2.apply(won_or_lost, axis=1)

    # Reorder columns
    cols = [
        "market_id", "app_name", "account_name", "am_name",
        "total_spend_90d", "non_vx_spend_90d", "vx_spend_90d", "vx_spend_pct_90d",
        "vx_rank_90d", "exchanges_above_vx_90d",
        "from_previous_analysis",
        "total_spend_30d", "non_vx_spend_30d", "vx_spend_30d", "vx_spend_pct_30d",
        "vx_rank_30d", "exchanges_above_vx_30d",
        "won_or_lost_rank_30d"
    ]
    p2 = p2[[c for c in cols if c in p2.columns]]
    p2 = p2.sort_values("total_spend_90d", ascending=False).reset_index(drop=True)

    output_path = f"{OUTPUT_DIR}/p2_rankings.csv"
    p2.to_csv(output_path, index=False)

    log("═" * 50, "STEP")
    log("PART 2 COMPLETE", "STEP")
    log(f"Output: {output_path}", "STEP")
    log("Next: run low_sov_part3_creative_split.py", "STEP")
    log("═" * 50, "STEP")