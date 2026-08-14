"""
Low Share of Wallet Analysis — Part 5: Ad Format Deep Dive

For 89 manually-curated apps with a true VX spend gap (>=$3K/30d) and VX rank >= 3,
fetches 30D Spot metrics across four dimensions and produces one wide row per app:

  1. Per ad format   : all exchanges ranked by spend
  2. VX Rank         : VX rank per ad format (newline-separated in one column)
  3. Per ad format   : VX and Non-VX top-5 geos (alternating VX / Non-VX per format)
  4. Per ad format   : VX and Non-VX top-5 advertiser categories
  5. Per ad format   : VX and Non-VX top-5 advertiser domains

Column layout (formats ordered by total 30d spend descending):
  market_id
  [Section A] {Format} - Top Exchanges      (one per format, all exchanges)
  [Section B] VX Rank (per Format)          (single column, all formats inside)
  [Section C] VX - {Format} - Top Geos      alternating with
              Non-VX - {Format} - Top Geos  per format
  [Section D] VX/Non-VX - {Format} - Top Categories  (alternating)
  [Section E] VX/Non-VX - {Format} - Top Domains     (alternating)

Output: output/low_sov_analysis/p5_deep_dive.csv
"""

import os
import requests
import pandas as pd
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

OUTPUT_DIR = "output/low_sov_analysis"
os.makedirs(OUTPUT_DIR, exist_ok=True)

DAYS = 30
VUNGLE_EXCHANGE = "VUNGLE"
SKIP_FORMATS = {"unknown_logical_size", "unmatched"}
TOP_N = 5

MARKET_IDS = [
    "319881193", "com.xiaomi.mipicks", "295646461", "281940292",
    "1407852246", "6751056652", "6504849169", "921765888",
    "com.truecaller", "395979574", "1544750895", "6747875092",
    "com.huub.tiger", "com.samsung.android.game.gamehome",
    "6754384144", "840919914", "com.miui.global.packageinstaller",
    "com.dubox.drive", "6465991018", "com.callapp.contacts",
    "com.playit.videoplayer", "512939461", "com.particlenews.newsbreak",
    "1589762792", "1584596271", "com.pixel.art.coloring.color.number",
    "498477945", "ball.sort.puzzle.color.sorting.bubble.games",
    "com.lemon.lvoverseas", "com.sec.android.app.samsungapps",
    "com.Gamnest.CandyRace", "com.storymatrix.drama", "6755183085",
    "com.netshort.abroad", "com.miniclip.carrom", "6471572249",
    "1509453185", "1459645446", "6754558455", "com.miui.msa.global",
    "com.loomgames.pixelflow", "kjv.bible.kingjamesbible", "1445472541",
    "com.hotmini.drama.hot", "com.easybrain.sudoku.android",
    "com.aws.android", "com.Hadiz.Holeucaneat",
    "com.fc.goods.sort.matching.puzzle.triplemaster", "com.michatapp.im",
    "videoplayer.videodownloader.downloader", "1212951043",
    "com.goldendragon.luckyblessing", "6478063606", "com.weaver.app.prod",
    "com.miui.player", "1357464684",
    "solitaire.patience.card.games.klondike.free", "1498889847",
    "6742221896", "jigsaw.puzzle.game.banana", "1624606445",
    "free.vpn.unblock.proxy.turbovpn", "6446005634", "1073936461",
    "com.naver.linewebtoon", "651510680", "com.domobile.applockwatcher",
    "1420058690", "water.sort.puzzle.game.color.sorting.free",
    "1562817072", "993090598", "6759763476",
    "puzzle.yarn.fever.unravel.puzzle", "com.sled.surfers.game",
    "video.downloader.videodownloader", "com.studio27.MelonPlayground",
    "com.ecffri.arrows",
    "sorting.games.goods.sort.triple.match3d.puzzle.stuff",
    "com.ludo.king", "com.vincentb.MobControl", "6444946155",
    "jp.naver.linemanga.android", "6451208928", "597088068",
    "com.mobilityware.spider", "1342112505", "1502447854",
    "com.UCMobile.intl", "1080465358",
]


# ── Utilities ─────────────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def fmt_spend(v):
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    elif v >= 1_000:
        return f"${v / 1_000:.1f}K"
    else:
        return f"${v:.0f}"


def fmt_items(pairs):
    """Format iterable of (label, spend) as 'LABEL ($X.XK), ...'"""
    return ", ".join(f"{name} ({fmt_spend(spend)})" for name, spend in pairs)


def fmt_label(ad_format):
    return {"mrec": "MREC"}.get(ad_format, ad_format.title())


# ── Looker ────────────────────────────────────────────────────────────────────

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


def run_query(token, fields, limit=500000):
    payload = {
        "model": "accelerate_analytics",
        "view": "accelerate_spot",
        "fields": fields,
        "filters": {
            "revenue_summary.event_date": f"{DAYS} days ago for {DAYS} days",
            "revenue_summary.source_app_app_store_id": ",".join(MARKET_IDS),
        },
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


# ── Data loading ──────────────────────────────────────────────────────────────

def parse_raw(data, col_names):
    """Parse raw Looker JSON into a clean DataFrame."""
    if not data:
        return pd.DataFrame(columns=col_names)
    df = pd.DataFrame(data)
    df.columns = col_names
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0)
    dim_cols = [c for c in col_names if c != "revenue"]
    for col in dim_cols:
        df[col] = df[col].fillna("").astype(str).str.strip()
    # Drop rows with missing key dimensions
    df = df[df["market_id"] != ""].copy()
    df["ad_format"] = df["ad_format"].str.lower()
    df = df[~df["ad_format"].isin(SKIP_FORMATS) & (df["ad_format"] != "")].copy()
    df = df[df["exchange"] != ""].copy()
    # Drop rows where any extra dimension column is empty
    extra_dim_cols = [c for c in col_names if c not in ("market_id", "ad_format", "exchange", "revenue")]
    for col in extra_dim_cols:
        df = df[df[col] != ""].copy()
    return df


def load_or_fetch(cache_path, fetch_fn):
    if os.path.exists(cache_path):
        log(f"Cache hit: {cache_path}")
        df = pd.read_csv(cache_path, dtype=str).fillna("")
        df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0)
        return df
    df = fetch_fn()
    df.to_csv(cache_path, index=False)
    log(f"Saved → {cache_path}")
    return df


def fetch_exchange(token):
    def _f():
        log("Fetching: ad_format × exchange (30d)...", "STEP")
        data = run_query(token, [
            "revenue_summary.source_app_app_store_id",
            "revenue_summary.ad_format",
            "revenue_summary.exchange",
            "revenue_summary.revenue",
        ])
        log(f"  {len(data)} rows returned")
        return parse_raw(data, ["market_id", "ad_format", "exchange", "revenue"])
    return load_or_fetch(f"{OUTPUT_DIR}/raw_p5_exchange.csv", _f)


def fetch_geo(token):
    def _f():
        log("Fetching: ad_format × exchange × country (30d)...", "STEP")
        data = run_query(token, [
            "revenue_summary.source_app_app_store_id",
            "revenue_summary.ad_format",
            "revenue_summary.exchange",
            "revenue_summary.country",
            "revenue_summary.revenue",
        ])
        log(f"  {len(data)} rows returned")
        return parse_raw(data, ["market_id", "ad_format", "exchange", "country", "revenue"])
    return load_or_fetch(f"{OUTPUT_DIR}/raw_p5_geo.csv", _f)


def fetch_category(token):
    def _f():
        log("Fetching: ad_format × exchange × category (30d)...", "STEP")
        data = run_query(token, [
            "revenue_summary.source_app_app_store_id",
            "revenue_summary.ad_format",
            "revenue_summary.exchange",
            "pinpoint__apps.category",
            "revenue_summary.revenue",
        ])
        log(f"  {len(data)} rows returned")
        return parse_raw(data, ["market_id", "ad_format", "exchange", "category", "revenue"])
    return load_or_fetch(f"{OUTPUT_DIR}/raw_p5_category.csv", _f)


def fetch_domain(token):
    def _f():
        log("Fetching: ad_format × exchange × domain (30d)...", "STEP")
        data = run_query(token, [
            "revenue_summary.source_app_app_store_id",
            "revenue_summary.ad_format",
            "revenue_summary.exchange",
            "pinpoint__apps.advertiser_domain",
            "revenue_summary.revenue",
        ])
        log(f"  {len(data)} rows returned")
        return parse_raw(data, ["market_id", "ad_format", "exchange", "domain", "revenue"])
    return load_or_fetch(f"{OUTPUT_DIR}/raw_p5_domain.csv", _f)


# ── Pivot builders ────────────────────────────────────────────────────────────

def build_exchange_pivot(df, ad_formats):
    """
    Section A: one column per format listing all exchanges by spend descending.
    Column name: "{Format} - Top Exchanges"
    e.g. "APPODEAL ($5.0K), VUNGLE ($3.2K), ..."
    """
    log("Building exchange pivot (Section A)...", "STEP")
    agg = df.groupby(["market_id", "ad_format", "exchange"])["revenue"].sum().reset_index()
    rows = defaultdict(dict)

    for fmt in ad_formats:
        col = f"{fmt_label(fmt)} - Top Exchanges"
        sub = agg[agg["ad_format"] == fmt].sort_values("revenue", ascending=False)
        for mid, grp in sub.groupby("market_id"):
            rows[mid][col] = fmt_items(zip(grp["exchange"], grp["revenue"]))

    return pd.DataFrame([{"market_id": mid, **rows.get(mid, {})} for mid in MARKET_IDS])


def build_vx_rank_col(df, ad_formats):
    """
    Section B: single column with VX rank per ad format, newline-separated.
    e.g.  "Interstitial - 3\nBanner - 1\nNative - Nil\nMREC - 2"
    """
    log("Building VX rank column (Section B)...", "STEP")
    agg = df.groupby(["market_id", "ad_format", "exchange"])["revenue"].sum().reset_index()
    agg["rank"] = agg.groupby(["market_id", "ad_format"])["revenue"].rank(
        method="dense", ascending=False
    ).astype(int)
    vx = agg[agg["exchange"].str.upper() == VUNGLE_EXCHANGE][["market_id", "ad_format", "rank"]]

    rows = {}
    for mid in MARKET_IDS:
        lines = []
        for fmt in ad_formats:
            match = vx[(vx["market_id"] == mid) & (vx["ad_format"] == fmt)]
            if match.empty:
                lines.append(f"{fmt_label(fmt)} - Nil")
            else:
                lines.append(f"{fmt_label(fmt)} - {match['rank'].iloc[0]}")
        rows[mid] = "\n".join(lines)

    return pd.DataFrame([{"market_id": mid, "VX Rank (per Format)": rows[mid]} for mid in MARKET_IDS])


def build_dim_pivot(df, dim_col, section_label, ad_formats):
    """
    Sections C/D/E: two columns per format (VX top-N and Non-VX top-N),
    alternating per format: VX Interstitial, Non-VX Interstitial, VX Native, ...
    Column names: "VX - {Format} - Top {Label}" / "Non-VX - {Format} - Top {Label}"
    """
    log(f"Building {section_label} pivot...", "STEP")
    agg = df.groupby(["market_id", "ad_format", "exchange", dim_col])["revenue"].sum().reset_index()
    rows = defaultdict(dict)

    for fmt in ad_formats:
        vx_col = f"VX - {fmt_label(fmt)} - Top {section_label}"
        nvx_col = f"Non-VX - {fmt_label(fmt)} - Top {section_label}"
        sub = agg[agg["ad_format"] == fmt]

        vx_agg = (
            sub[sub["exchange"].str.upper() == VUNGLE_EXCHANGE]
            .groupby(["market_id", dim_col])["revenue"].sum().reset_index()
        )
        nvx_agg = (
            sub[sub["exchange"].str.upper() != VUNGLE_EXCHANGE]
            .groupby(["market_id", dim_col])["revenue"].sum().reset_index()
        )

        for mid, grp in vx_agg.groupby("market_id"):
            top = grp.nlargest(TOP_N, "revenue")
            rows[mid][vx_col] = fmt_items(zip(top[dim_col], top["revenue"]))

        for mid, grp in nvx_agg.groupby("market_id"):
            top = grp.nlargest(TOP_N, "revenue")
            rows[mid][nvx_col] = fmt_items(zip(top[dim_col], top["revenue"]))

    return pd.DataFrame([{"market_id": mid, **rows.get(mid, {})} for mid in MARKET_IDS])


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    log("═" * 55, "STEP")
    log("LOW SOV ANALYSIS — PART 5: AD FORMAT DEEP DIVE", "STEP")
    log("═" * 55, "STEP")
    log(f"Apps: {len(MARKET_IDS)}, Window: {DAYS}d")

    # Only authenticate if at least one cache file is missing
    cache_paths = [
        f"{OUTPUT_DIR}/raw_p5_exchange.csv",
        f"{OUTPUT_DIR}/raw_p5_geo.csv",
        f"{OUTPUT_DIR}/raw_p5_category.csv",
        f"{OUTPUT_DIR}/raw_p5_domain.csv",
    ]
    needs_fetch = any(not os.path.exists(p) for p in cache_paths)
    token = get_looker_token() if needs_fetch else None

    # ── Fetch ──
    df_ex = fetch_exchange(token)
    df_geo = fetch_geo(token)
    df_cat = fetch_category(token)
    df_dom = fetch_domain(token)

    # Determine format order by total 30d spend descending
    fmt_order = (
        df_ex.groupby("ad_format")["revenue"]
        .sum()
        .sort_values(ascending=False)
        .index.tolist()
    )
    log(f"Ad formats found (spend order): {fmt_order}")

    # ── Build pivots ──
    p_ex = build_exchange_pivot(df_ex, fmt_order)
    p_rank = build_vx_rank_col(df_ex, fmt_order)
    p_geo = build_dim_pivot(df_geo, "country", "Geos", fmt_order)
    p_cat = build_dim_pivot(df_cat, "category", "Categories", fmt_order)
    p_dom = build_dim_pivot(df_dom, "domain", "Domains", fmt_order)

    # ── Merge ──
    log("Merging all sections...", "STEP")
    result = p_ex
    for p in [p_rank, p_geo, p_cat, p_dom]:
        result = result.merge(p, on="market_id", how="left")

    # ── Final column order ──
    # Section A: {Format} - Top Exchanges  (one per format)
    # Section B: VX Rank (per Format)      (single column)
    # Section C/D/E: alternating VX/Non-VX per format within each section
    def alternating_dim_cols(section, formats):
        cols = []
        for f in formats:
            cols.append(f"VX - {fmt_label(f)} - Top {section}")
            cols.append(f"Non-VX - {fmt_label(f)} - Top {section}")
        return cols

    col_order = (
        ["market_id"]
        + [f"{fmt_label(f)} - Top Exchanges" for f in fmt_order]
        + ["VX Rank (per Format)"]
        + alternating_dim_cols("Geos", fmt_order)
        + alternating_dim_cols("Categories", fmt_order)
        + alternating_dim_cols("Domains", fmt_order)
    )
    result = result[[c for c in col_order if c in result.columns]]

    # ── Save ──
    out_path = f"{OUTPUT_DIR}/p5_deep_dive.csv"
    result.to_csv(out_path, index=False)

    log("═" * 55, "STEP")
    log("PART 5 COMPLETE", "STEP")
    log(f"Output: {out_path}")
    log(f"  {len(result)} apps × {len(result.columns)} columns")
    log("═" * 55, "STEP")
