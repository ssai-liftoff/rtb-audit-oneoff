"""
RTB Platform Parity Audit — Part 6: Fetch RTB Connection × Allowed App Spend

For each row in the P5 audit, fetches actual unified ad spend on that specific
RTB connection for the allowed app (vx_analytics / vx_overview).

Input:  output/rtb_platform_parity_audit/p5_platform_parity_audit.csv
Output: output/rtb_platform_parity_audit/p6_platform_parity_audit.csv
        output/rtb_platform_parity_audit/p6_combo_spend_raw.csv  (cache)
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


def fetch_combo_spend_batch(token, app_ids, connection_ids):
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=auth_headers(token),
        json={
            "model": "vx_analytics",
            "view": "vx_overview",
            "fields": [
                "publisher_apps.id",
                "rtb_connections.id",
                "vx_overview.unified_ad_spend",
            ],
            "filters": {
                "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
                "publisher_apps.id": ",".join(app_ids),
                "rtb_connections.id": ",".join(connection_ids),
            },
            "limit": "100000",
        },
        timeout=300,
    )
    if not resp.ok:
        log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
        resp.raise_for_status()
    return resp.json()


def fetch_all_combo_spend(token, app_ids, connection_ids):
    raw_cache = f"{OUTPUT_DIR}/p6_combo_spend_raw.csv"
    if os.path.exists(raw_cache):
        log(f"Loading combo spend cache → {raw_cache}")
        return pd.read_csv(raw_cache)

    batches = [app_ids[i : i + BATCH_SIZE] for i in range(0, len(app_ids), BATCH_SIZE)]
    log(f"Fetching RTB × app spend for {len(app_ids):,} apps × {len(connection_ids)} connections...")
    log(f"  {len(batches)} Looker batch(es)")

    all_rows = []
    for i, batch in enumerate(batches, 1):
        log(f"  Batch {i}/{len(batches)} ({len(batch)} apps)...")
        rows = fetch_combo_spend_batch(token, batch, connection_ids)
        all_rows.extend(rows)
        log(f"    → {len(rows):,} rows")

    if not all_rows:
        return pd.DataFrame(
            columns=[
                "rtb_connection_id",
                "allowed_app_id",
                "total_spend_7d",
                "allowed_app_rtb_daily_spend",
            ]
        )

    df = pd.DataFrame(all_rows)
    df.columns = ["allowed_app_id", "rtb_connection_id", "total_spend_7d"]
    df["allowed_app_id"] = df["allowed_app_id"].astype(str).str.strip()
    df["rtb_connection_id"] = df["rtb_connection_id"].astype(str).str.strip()
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)

    df = (
        df.groupby(["rtb_connection_id", "allowed_app_id"], as_index=False)
        .agg(total_spend_7d=("total_spend_7d", "sum"))
    )
    df["allowed_app_rtb_daily_spend"] = (df["total_spend_7d"] / LOOKBACK_DAYS).round(2)

    df.to_csv(raw_cache, index=False)
    log(f"Cached raw combo spend → {raw_cache} ({len(df):,} rows)")
    return df


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    p5_path = f"{OUTPUT_DIR}/p5_platform_parity_audit.csv"
    out_path = f"{OUTPUT_DIR}/p6_platform_parity_audit.csv"

    if not os.path.exists(p5_path):
        raise FileNotFoundError(f"Not found: {p5_path} — run p5_build_audit.py first")

    log("═" * 60, "STEP")
    log("RTB PLATFORM PARITY — PART 6: FETCH COMBO SPEND", "STEP")
    log("═" * 60, "STEP")

    p5 = pd.read_csv(p5_path)
    p5["rtb_connection_id"] = p5["rtb_connection_id"].astype(str).str.strip()
    p5["allowed_app_id"] = p5["allowed_app_id"].astype(str).str.strip()

    app_ids = sorted(p5["allowed_app_id"].unique().tolist())
    connection_ids = sorted(p5["rtb_connection_id"].unique().tolist())
    log(f"P5 rows:              {len(p5):,}")
    log(f"Unique allowed apps:  {len(app_ids):,}")
    log(f"Unique connections:   {len(connection_ids)}")

    refresh = "--refresh" in __import__("sys").argv
    raw_cache = f"{OUTPUT_DIR}/p6_combo_spend_raw.csv"
    if refresh and os.path.exists(raw_cache):
        os.remove(raw_cache)
        log("Removed combo spend cache (--refresh)", "WARN")

    token = get_token()
    log("Authenticated with Looker")
    combo_spend = fetch_all_combo_spend(token, app_ids, connection_ids)

    out = p5.merge(
        combo_spend[
            ["rtb_connection_id", "allowed_app_id", "total_spend_7d", "allowed_app_rtb_daily_spend"]
        ].rename(columns={"total_spend_7d": "allowed_app_rtb_spend_7d"}),
        on=["rtb_connection_id", "allowed_app_id"],
        how="left",
    )
    out["allowed_app_rtb_spend_7d"] = out["allowed_app_rtb_spend_7d"].fillna(0)
    out["allowed_app_rtb_daily_spend"] = out["allowed_app_rtb_daily_spend"].fillna(0)

    out = out.sort_values(
        ["allowed_app_rtb_daily_spend", "missed_daily_spend", "connection_daily_spend"],
        ascending=False,
    ).reset_index(drop=True)

    out.to_csv(out_path, index=False)

    has_spend = (out["allowed_app_rtb_daily_spend"] > 0).sum()
    log("═" * 60, "STEP")
    log(f"Rows with RTB combo spend > $0: {has_spend:,} / {len(out):,}")
    log(f"Total allowed-app RTB daily spend: ${out['allowed_app_rtb_daily_spend'].sum():,.0f}")
    log(f"Saved → {out_path}")

    if has_spend:
        log("Top 5 by allowed-app RTB daily spend:", "STEP")
        for _, row in out[out["allowed_app_rtb_daily_spend"] > 0].head(5).iterrows():
            log(
                f"  {row['rtb_connection_name'][:35]:<35} "
                f"{row['allowed_app_name'][:25]:<25} "
                f"${row['allowed_app_rtb_daily_spend']:,.0f}/day on RTB"
            )

    log("═" * 60, "STEP")
