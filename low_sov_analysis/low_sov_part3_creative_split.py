"""
Low Share of Wallet Analysis — Part 3: Creative Type Split + Rankings

For each qualifying app:
  - VX video vs non-video spend split (interstitial = video, rest = non-video)
  - Non-VX video vs non-video spend split (all exchanges aggregated)
  - VX rank for video spend vs all exchanges
  - VX rank for non-video spend vs all exchanges
  - Exchanges above VX for video and non-video

Skips: UNKNOWN_LOGICAL_SIZE, UNMATCHED ad formats

Reads:  output/low_sov_analysis/p2_rankings.csv
Writes: output/low_sov_analysis/raw_creative_90d.csv
        output/low_sov_analysis/raw_creative_30d.csv
        output/low_sov_analysis/p3_creative_split.csv
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
VIDEO_FORMATS = {"interstitial"}
NON_VIDEO_FORMATS = {"banner", "mrec", "native"}
SKIP_FORMATS = {"unknown_logical_size", "unmatched"}


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


def run_query(token, model, view, fields, filters, sorts=None, limit=200000):
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


# ── Fetch creative spend per app × exchange × ad_format ──────────────────────

def fetch_creative_spend(token, qualifying_apps, days, label):
    cache = f"{OUTPUT_DIR}/raw_creative_{label}.csv"
    if os.path.exists(cache):
        log(f"Loading {label} creative data from cache: {cache}")
        return pd.read_csv(cache)

    log(f"Fetching {label} creative spend per app × exchange × ad_format...", "STEP")
    log("Please wait...")

    id_filter = ",".join(list(qualifying_apps)[:5000])

    data = run_query(
        token,
        model="accelerate_analytics",
        view="accelerate_spot",
        fields=[
            "revenue_summary.source_app_app_store_id",
            "revenue_summary.exchange",
            "revenue_summary.ad_format",
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
    df.columns = ["market_id", "exchange", "ad_format", "revenue"]
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0)
    df = df[df["market_id"].notna() & (df["market_id"] != "")].copy()
    df = df[df["exchange"].notna() & (df["exchange"] != "")].copy()
    df = df[df["ad_format"].notna() & (df["ad_format"] != "")].copy()

    # Normalise ad_format to lowercase
    df["ad_format"] = df["ad_format"].str.lower().str.strip()

    # Skip unknown/unmatched formats
    df = df[~df["ad_format"].isin(SKIP_FORMATS)].copy()

    # Tag as video or non-video
    df["creative_type"] = df["ad_format"].apply(
        lambda x: "video" if x in VIDEO_FORMATS else "non_video"
    )

    df.to_csv(cache, index=False)
    log(f"Saved {label} creative data → {cache}")
    return df


# ── Calculate VX and Non-VX creative splits ───────────────────────────────────

def calculate_creative_splits(df, label):
    log(f"Calculating VX and Non-VX creative splits ({label})...", "STEP")

    # ── VX split ──────────────────────────────────────────────────────────────
    vx_df = df[df["exchange"].str.upper() == VUNGLE_EXCHANGE].copy()

    vx_pivot = vx_df.groupby(["market_id", "creative_type"])["revenue"].sum().unstack(
        fill_value=0
    ).reset_index()
    vx_pivot.columns.name = None

    if "video" not in vx_pivot.columns:
        vx_pivot["video"] = 0
    if "non_video" not in vx_pivot.columns:
        vx_pivot["non_video"] = 0

    vx_pivot = vx_pivot.rename(columns={
        "video": f"vx_video_spend_{label}",
        "non_video": f"vx_non_video_spend_{label}"
    })
    vx_pivot[f"vx_total_creative_spend_{label}"] = (
        vx_pivot[f"vx_video_spend_{label}"] + vx_pivot[f"vx_non_video_spend_{label}"]
    )
    vx_pivot[f"vx_video_pct_{label}"] = (
        vx_pivot[f"vx_video_spend_{label}"] / vx_pivot[f"vx_total_creative_spend_{label}"] * 100
    ).round(2).where(vx_pivot[f"vx_total_creative_spend_{label}"] > 0, 0)
    vx_pivot[f"vx_non_video_pct_{label}"] = (
        vx_pivot[f"vx_non_video_spend_{label}"] / vx_pivot[f"vx_total_creative_spend_{label}"] * 100
    ).round(2).where(vx_pivot[f"vx_total_creative_spend_{label}"] > 0, 0)

    # ── Non-VX split (all other exchanges aggregated) ─────────────────────────
    non_vx_df = df[df["exchange"].str.upper() != VUNGLE_EXCHANGE].copy()

    non_vx_pivot = non_vx_df.groupby(["market_id", "creative_type"])["revenue"].sum().unstack(
        fill_value=0
    ).reset_index()
    non_vx_pivot.columns.name = None

    if "video" not in non_vx_pivot.columns:
        non_vx_pivot["video"] = 0
    if "non_video" not in non_vx_pivot.columns:
        non_vx_pivot["non_video"] = 0

    non_vx_pivot = non_vx_pivot.rename(columns={
        "video": f"non_vx_video_spend_{label}",
        "non_video": f"non_vx_non_video_spend_{label}"
    })
    non_vx_pivot[f"non_vx_total_creative_spend_{label}"] = (
        non_vx_pivot[f"non_vx_video_spend_{label}"] + non_vx_pivot[f"non_vx_non_video_spend_{label}"]
    )
    non_vx_pivot[f"non_vx_video_pct_{label}"] = (
        non_vx_pivot[f"non_vx_video_spend_{label}"] / non_vx_pivot[f"non_vx_total_creative_spend_{label}"] * 100
    ).round(2).where(non_vx_pivot[f"non_vx_total_creative_spend_{label}"] > 0, 0)
    non_vx_pivot[f"non_vx_non_video_pct_{label}"] = (
        non_vx_pivot[f"non_vx_non_video_spend_{label}"] / non_vx_pivot[f"non_vx_total_creative_spend_{label}"] * 100
    ).round(2).where(non_vx_pivot[f"non_vx_total_creative_spend_{label}"] > 0, 0)

    result = vx_pivot.merge(non_vx_pivot, on="market_id", how="outer")
    log(f"  Creative split complete: {len(result)} apps")
    return result


# ── Calculate video and non-video rankings (all exchanges) ────────────────────

def calculate_creative_rankings(df, label):
    log(f"Calculating video/non-video exchange rankings ({label})...", "STEP")

    # Aggregate per app × exchange × creative_type
    agg = df.groupby(["market_id", "exchange", "creative_type"])["revenue"].sum().reset_index()

    results = []

    for creative_type, col_suffix in [("video", "video"), ("non_video", "non_video")]:
        type_df = agg[agg["creative_type"] == creative_type].copy()

        # Rank exchanges per app
        type_df["rank"] = type_df.groupby("market_id")["revenue"].rank(
            method="dense", ascending=False
        ).astype(int)

        # VX rank
        vx_rank = type_df[type_df["exchange"].str.upper() == VUNGLE_EXCHANGE][
            ["market_id", "rank"]
        ].copy()
        vx_rank.columns = ["market_id", f"vx_{col_suffix}_rank_{label}"]

        # Exchanges above VX
        def exchanges_above(group):
            market_id = group.name
            vx_row = vx_rank[vx_rank["market_id"] == market_id]
            if vx_row.empty:
                return "VX not present"
            vx_r = vx_row[f"vx_{col_suffix}_rank_{label}"].iloc[0]
            above = group[group["rank"] < vx_r].sort_values("rank")["exchange"].tolist()
            return ", ".join(above) if above else ""

        above_df = type_df.groupby("market_id").apply(
            exchanges_above
        ).reset_index()
        above_df.columns = ["market_id", f"exchanges_above_vx_{col_suffix}_{label}"]

        ranked = vx_rank.merge(above_df, on="market_id", how="outer")
        ranked[f"vx_{col_suffix}_rank_{label}"] = ranked[f"vx_{col_suffix}_rank_{label}"].fillna(-1).astype(int)
        ranked[f"exchanges_above_vx_{col_suffix}_{label}"] = ranked[f"exchanges_above_vx_{col_suffix}_{label}"].fillna("VX not present")

        log(f"  {creative_type} — VX #1: {(ranked[f'vx_{col_suffix}_rank_{label}'] == 1).sum()}, "
            f"VX #2+: {(ranked[f'vx_{col_suffix}_rank_{label}'] > 1).sum()}, "
            f"VX absent: {(ranked[f'vx_{col_suffix}_rank_{label}'] == -1).sum()}")

        results.append(ranked)

    return results[0].merge(results[1], on="market_id", how="outer")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    log("═" * 50, "STEP")
    log("LOW SHARE OF WALLET ANALYSIS — PART 3", "STEP")
    log("═" * 50, "STEP")

    p2_path = f"{OUTPUT_DIR}/p2_rankings.csv"
    if not os.path.exists(p2_path):
        raise FileNotFoundError(f"Part 2 output not found: {p2_path} — run Part 2 first")

    log("Loading qualifying apps from Part 2...", "STEP")
    p2 = pd.read_csv(p2_path)
    qualifying_apps = set(p2["market_id"].tolist())
    log(f"  {len(qualifying_apps)} qualifying apps loaded")

    token = get_looker_token()

    # Fetch creative spend
    df_creative_90d = fetch_creative_spend(token, qualifying_apps, DAYS_90, "90d")
    df_creative_30d = fetch_creative_spend(token, qualifying_apps, DAYS_30, "30d")

    # Calculate splits and rankings
    splits_90d = calculate_creative_splits(df_creative_90d, "90d")
    rankings_90d = calculate_creative_rankings(df_creative_90d, "90d")

    splits_30d = calculate_creative_splits(df_creative_30d, "30d")
    rankings_30d = calculate_creative_rankings(df_creative_30d, "30d")

    # Merge everything into Part 2
    log("Merging creative data into Part 2 output...", "STEP")
    p3 = p2.copy()
    p3 = p3.merge(splits_90d, on="market_id", how="left")
    p3 = p3.merge(rankings_90d, on="market_id", how="left")
    p3 = p3.merge(splits_30d, on="market_id", how="left")
    p3 = p3.merge(rankings_30d, on="market_id", how="left")

    # Fill missing
    for col in p3.columns:
        if "spend" in col or "pct" in col:
            p3[col] = p3[col].fillna(0)
        if "rank" in col:
            p3[col] = p3[col].fillna(-1)
        if "exchanges_above" in col:
            p3[col] = p3[col].fillna("VX not present")

    # Reorder columns
    cols = [
        "market_id", "app_name", "account_name", "am_name",
        # 90d overall
        "total_spend_90d", "non_vx_spend_90d", "vx_spend_90d", "vx_spend_pct_90d",
        "vx_rank_90d", "exchanges_above_vx_90d",
        # 90d creative
        "vx_video_spend_90d", "vx_video_pct_90d",
        "vx_non_video_spend_90d", "vx_non_video_pct_90d",
        "non_vx_video_spend_90d", "non_vx_video_pct_90d",
        "non_vx_non_video_spend_90d", "non_vx_non_video_pct_90d",
        "vx_video_rank_90d", "exchanges_above_vx_video_90d",
        "vx_non_video_rank_90d", "exchanges_above_vx_non_video_90d",
        "from_previous_analysis",
        # 30d overall
        "total_spend_30d", "non_vx_spend_30d", "vx_spend_30d", "vx_spend_pct_30d",
        "vx_rank_30d", "exchanges_above_vx_30d",
        "won_or_lost_rank_30d",
        # 30d creative
        "vx_video_spend_30d", "vx_video_pct_30d",
        "vx_non_video_spend_30d", "vx_non_video_pct_30d",
        "non_vx_video_spend_30d", "non_vx_video_pct_30d",
        "non_vx_non_video_spend_30d", "non_vx_non_video_pct_30d",
        "vx_video_rank_30d", "exchanges_above_vx_video_30d",
        "vx_non_video_rank_30d", "exchanges_above_vx_non_video_30d",
    ]
    p3 = p3[[c for c in cols if c in p3.columns]]
    p3 = p3.sort_values("total_spend_90d", ascending=False).reset_index(drop=True)

    output_path = f"{OUTPUT_DIR}/p3_creative_split.csv"
    p3.to_csv(output_path, index=False)

    log("═" * 50, "STEP")
    log("PART 3 COMPLETE", "STEP")
    log(f"Output: {output_path}", "STEP")
    log(f"Total columns: {len(p3.columns)}", "STEP")
    log("Next: run low_sov_part4_final.py to stitch and finalise", "STEP")
    log("═" * 50, "STEP")