"""
FIFA Blocklist Audit — Part 2: Fetch FIFA Advertiser Profiles

For each FIFA adomain in fifa_adomains.csv, fetches:
  - IAB category codes reported via vx_analytics/vx_overview
  - Total VX spend over the last 7 days (uplift proxy)

Output: one row per adomain with all observed IAB codes (comma-separated) and
aggregated spend. Adomains with no Looker spend in the window will appear with
null spend/IAB — domain-block checking still works for those in p4, but category
block checking will be skipped.

Source: vx_analytics / vx_overview
Output: output/fifa_blocklist_audit/p2_advertiser_profiles.csv
"""

import os
import re
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

IAB_CODE_RE = re.compile(r"^IAB\d")

load_dotenv()

LOOKER_BASE_URL      = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID     = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

OUTPUT_DIR    = "output/fifa_blocklist_audit"
ADOMAINS_FILE = "fifa_blocklist_audit/fifa_adomains.csv"
LOOKBACK_DAYS = 7
PAGE_SIZE     = 10_000

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


def run_query(token, body):
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=auth_headers(token),
        json={**body, "limit": str(PAGE_SIZE)},
        timeout=120
    )
    resp.raise_for_status()
    return resp.json()


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    cache     = f"{OUTPUT_DIR}/p2_advertiser_profiles.csv"
    iab_cache = f"{OUTPUT_DIR}/p2_adomain_iab_spend.csv"

    if os.path.exists(cache) and os.path.exists(iab_cache):
        log(f"Cache found — loading {cache}")
        df = pd.read_csv(cache)
        log(f"  {len(df):,} adomain profiles loaded")
    else:
        log("═" * 60, "STEP")
        log("FIFA BLOCKLIST AUDIT — PART 2: FETCH ADVERTISER PROFILES", "STEP")
        log("═" * 60, "STEP")

        adomains = pd.read_csv(ADOMAINS_FILE)["adomain"].str.strip().dropna().unique().tolist()
        log(f"FIFA adomains to profile: {len(adomains)}")

        token = get_token()
        log("Authenticated with Looker")

        # Looker IN filter: comma-separated values
        adomain_filter = ",".join(adomains)

        query_body = {
            "model": "vx_analytics",
            "view":  "vx_overview",
            "fields": [
                "vx_overview.adomain",
                "vx_overview.content_category_code",
                "vx_overview.unified_ad_spend",
            ],
            "filters": {
                "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
                "vx_overview.adomain":    adomain_filter,
                "rtb_accounts.name":      "Exchange - RTB - Liftoff,Exchange - RTB - Moloco",
            },
            "sorts": ["vx_overview.unified_ad_spend desc"],
        }

        log(f"Fetching adomain profiles from vx_overview (last {LOOKBACK_DAYS} days)...")
        rows = run_query(token, query_body)
        log(f"  → {len(rows):,} rows returned")

        if not rows:
            log("No rows returned — all adomains may have zero spend in window", "WARN")
            raw = pd.DataFrame(columns=["adomain", "iab_code", "spend"])
        else:
            raw = pd.DataFrame(rows)
            raw.columns = ["adomain", "iab_code", "spend"]
            raw["spend"]   = pd.to_numeric(raw["spend"], errors="coerce").fillna(0)
            raw["adomain"] = raw["adomain"].str.strip().str.lower()
            raw["iab_code"] = raw["iab_code"].astype(str).str.strip()

        # Save per-(adomain × IAB code) spend breakdown for category-specific uplift in p4
        valid_mask = raw["iab_code"].apply(lambda c: bool(IAB_CODE_RE.match(c)) and len(c) <= 10)
        iab_spend = raw[valid_mask].copy()
        iab_spend["daily_spend"] = (iab_spend["spend"] / LOOKBACK_DAYS).round(4)
        iab_spend = iab_spend.rename(columns={"spend": "total_7d_spend"})[
            ["adomain", "iab_code", "total_7d_spend", "daily_spend"]
        ]
        iab_spend.to_csv(iab_cache, index=False)
        log(f"Saved → {iab_cache}  ({len(iab_spend):,} adomain×IAB rows)")

        # Aggregate to one row per adomain for the main profiles file
        def agg_profile(group):
            all_codes = group["iab_code"].dropna().astype(str).str.strip().unique().tolist()
            codes = sorted(c for c in all_codes if IAB_CODE_RE.match(c) and len(c) <= 10)
            return pd.Series({
                "iab_codes":      ",".join(codes) if codes else "",
                "total_7d_spend": round(group["spend"].sum(), 2),
                "daily_spend":    round(group["spend"].sum() / LOOKBACK_DAYS, 2),
            })

        if len(raw):
            profiles = raw.groupby("adomain", sort=False).apply(agg_profile).reset_index()
        else:
            profiles = pd.DataFrame(columns=["adomain", "iab_codes", "total_7d_spend", "daily_spend"])

        # Ensure every adomain from the input file appears in output
        all_adomains_df = pd.DataFrame({"adomain": [a.lower() for a in adomains]})
        df = all_adomains_df.merge(profiles, on="adomain", how="left")
        df["total_7d_spend"] = df["total_7d_spend"].fillna(0)
        df["daily_spend"]    = df["daily_spend"].fillna(0)
        df["iab_codes"]      = df["iab_codes"].fillna("")

        df.to_csv(cache, index=False)
        log(f"Saved → {cache}")

    log("═" * 60, "STEP")
    found    = (df["iab_codes"] != "").sum()
    no_spend = (df["daily_spend"] == 0).sum()
    log(f"Total adomains:           {len(df):,}")
    log(f"With IAB codes in Looker: {found:,}")
    log(f"No spend in window:       {no_spend:,}  (domain-block check only)")
    log(f"Total 7d spend (all):     ${df['total_7d_spend'].sum():,.0f}")
    if len(df):
        log("Top adomains by spend:")
        for _, row in df.nlargest(5, "daily_spend").iterrows():
            log(f"  {row['adomain']:<40} ${row['daily_spend']:>10,.0f}/day  [{row['iab_codes']}]")
    log("Next: run p3_fetch_api_blocklists.py", "STEP")
    log("═" * 60, "STEP")
