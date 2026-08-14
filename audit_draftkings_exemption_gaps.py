"""
DraftKings Exemption Gap Audit — Part 1: Missing combo coverage

For high-spend publisher apps (≥$500/day) that already have at least one
DraftKings V1-6 blocklist exemption, checks whether all reference combos
(domain × RTB account × geo) from draftkings_combos.csv are configured.

Each expected combo is named Combo1, Combo2, … (all network combos ≥ $100/day).
Output rows are one per missing combo per app, with uplift:

  est_uplift_daily = (combo_daily_spend / network_daily_spend) × pub_daily_spend

Inputs:
  draftkings_exemption_audit/draftkings_combos.csv  (auto-fetched top-9 from vx_overview)
  output/pub_blocked_categories/p1_apps.csv         (or re-fetch supply)

Fetch combos only:
  python3 -u audit_draftkings_exemption_gaps.py --fetch-combos --fetch-combos-only

Output:
  output/draftkings_exemption_audit/audit_draftkings_missing_combos.csv
  output/draftkings_exemption_audit/audit_draftkings_missing_combos_by_app.csv
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

DASHBOARD_API_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2NvdW50IjoiNjgxM2JmZDUxMDRiNDAwMDExMzcyNDM2Iiw"
    "iaXNNYW5hZ2VkQWNjb3VudCI6ZmFsc2UsImFwaVRva2VuIjoiN2FhMThlNTdmMTU0NmQ2YmQ1NDJlMDc2YWI5NzI3"
    "YzMiLCJlbWFpbCI6InNzYWlAbGlmdG9mZi5pbyIsImZlYXR1cmVzIjpbImZlYXR1cmVfbWVkaWF0aW9uX2FjY2Vzc"
    "yJdLCJpZCI6IjY4MTNiZmQ1MTA0YjQwMDAxMTM3MjQzYSIsImlzX2RlbGV0ZWQiOmZhbHNlLCJpc0ludGVybmFsIjp"
    "0cnVlLCJyb2xlIjoiYWNjb3VudF9zdHJhdGVneSIsInNvdXJjZSI6IiIsInVpZCI6IjAwMDAwMDAwMDAwMDAwMDAwMD"
    "AwMDAwMCIsImV4cCI6MTc4NjAxODk3Nn0.3AXzcCdsTDjmKafDWx-szQz6D5w5avBG3R45_IOu92Q"
)
DASHBOARD_API_URL = "https://pub-gateway-api.vungle.com/query"
DASHBOARD_HEADERS = {
    "authorization": f"Bearer {DASHBOARD_API_TOKEN}",
    "content-type": "application/json",
    "accept": "application/json, text/plain, */*",
    "vungle-source": "admin",
    "vungle-version": "1",
}

LOOKBACK_DAYS = 7
SPEND_THRESHOLD = 500
TOTAL_THRESHOLD = SPEND_THRESHOLD * LOOKBACK_DAYS
PAGE_SIZE = 50_000
APP_BATCH = 100
MAX_WORKERS = 25
COMBO_MIN_DAILY_SPEND = 100
GAMBLING_CAT = "V1-6"
DK_DOMAIN_HINT = "draftkings"

LOOKER_FIELD_ADOMAIN = "vx_overview.adomain"
LOOKER_FIELD_GEO = "geos.code"
LOOKER_FIELD_RTB = "rtb_accounts.id"
LOOKER_FIELD_SPEND = "vx_overview.unified_ad_spend"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
COMBOS_CSV = os.path.join(SCRIPT_DIR, "draftkings_exemption_audit", "draftkings_combos.csv")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "draftkings_exemption_audit")
SUPPLY_CACHE = os.path.join(SCRIPT_DIR, "output", "pub_blocked_categories", "p1_apps.csv")
DOMAIN_SUPPLY_CACHE = os.path.join(SCRIPT_DIR, "output", "pub_blocked_domains", "p1_apps.csv")
NETWORK_TOTAL_CACHE = os.path.join(SCRIPT_DIR, "output", "pub_blocked_domains", "p3_network_total.csv")

os.makedirs(OUTPUT_DIR, exist_ok=True)

CACHE_APPS = f"{OUTPUT_DIR}/p1_apps.csv"
CACHE_EXEMPTIONS = f"{OUTPUT_DIR}/p2_app_exemptions.csv"
CACHE_FETCHED = f"{OUTPUT_DIR}/p2_apps_fetched.csv"
CACHE_DONE = f"{CACHE_EXEMPTIONS}.done"
CACHE_COMBOS_RAW = f"{OUTPUT_DIR}/p3_draftkings_combos_raw.csv"
CACHE_APP_COMBO_SPEND = f"{OUTPUT_DIR}/p3_app_combo_spend.csv"
OUTPUT_FILE = f"{OUTPUT_DIR}/audit_draftkings_missing_combos.csv"
OUTPUT_FILE_APP = f"{OUTPUT_DIR}/audit_draftkings_missing_combos_by_app.csv"

APP_QUERY = """
query application($id: String!) {
  application(id: $id) {
    id
    owner
    adCatBlocklist
    blocklistExemptions {
      advCatId
      domain
      bundle
      rtbAccountId
      country
    }
  }
}
"""


def log(msg, level="INFO"):
    from datetime import datetime

    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def norm_domain(val):
    return str(val or "").strip().lower()


def norm_rtb(val):
    return str(val or "").strip()


def norm_country(val):
    return str(val or "").strip().upper()


def combo_key(domain, rtb_account_id, country):
    return (norm_domain(domain), norm_rtb(rtb_account_id), norm_country(country))


def parse_exemptions(val):
    if isinstance(val, list):
        return val
    if pd.isna(val) or val == "":
        return []
    try:
        parsed = json.loads(val)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return []


def load_combos(force_fetch=False):
    if (
        not force_fetch
        and "--fetch-combos" not in sys.argv
        and os.path.exists(COMBOS_CSV)
    ):
        df = _read_combos_csv(COMBOS_CSV)
        if (df["network_daily_spend"] > 0).any():
            return df

    token = get_looker_token()
    df = fetch_draftkings_combos(token)
    return df


def _read_combos_csv(path):
    df = pd.read_csv(path, dtype=str)
    required = ["combo_id", "domain", "rtb_account_id", "country", "network_daily_spend"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"draftkings_combos.csv missing columns: {missing}")

    df["combo_id"] = df["combo_id"].astype(str).str.strip()
    df["domain"] = df["domain"].fillna("").astype(str).str.strip()
    df["rtb_account_id"] = df["rtb_account_id"].fillna("").astype(str).str.strip()
    df["country"] = df["country"].fillna("").astype(str).str.strip()
    df["network_daily_spend"] = pd.to_numeric(df["network_daily_spend"], errors="coerce").fillna(0)

    if df.empty:
        raise ValueError("draftkings_combos.csv is empty")
    if df["combo_id"].duplicated().any():
        dupes = df.loc[df["combo_id"].duplicated(), "combo_id"].tolist()
        raise ValueError(f"Duplicate combo_id values: {dupes}")

    df["combo_key"] = df.apply(
        lambda r: combo_key(r["domain"], r["rtb_account_id"], r["country"]), axis=1
    )
    log(f"Loaded {len(df)} reference combos from {path}")
    return df


def looker_rows_to_df(raw, rename_map):
    if not raw:
        return pd.DataFrame(columns=list(rename_map.values()))
    df = pd.DataFrame(raw)
    missing = [src for src in rename_map if src not in df.columns]
    if missing:
        raise RuntimeError(f"Looker response missing expected fields: {missing}")
    return df.rename(columns=rename_map)


def run_looker_query(token, model, view, fields, filters, sorts=None, limit=PAGE_SIZE):
    payload = {
        "model": model,
        "view": view,
        "fields": fields,
        "filters": filters,
        "limit": str(limit),
    }
    if sorts:
        payload["sorts"] = sorts
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=600,
    )
    if not resp.ok:
        log(f"Looker error {resp.status_code}: {resp.text[:500]}", "ERROR")
        resp.raise_for_status()
    return resp.json()


def fetch_draftkings_combos(token):
    log(
        f"Fetching DraftKings spend combos from vx_overview (≥ ${COMBO_MIN_DAILY_SPEND:,}/day)...",
        "STEP",
    )
    date_filter = f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
    fields = [LOOKER_FIELD_ADOMAIN, LOOKER_FIELD_RTB, LOOKER_FIELD_GEO, LOOKER_FIELD_SPEND]
    filters = {
        "vx_overview.event_date": date_filter,
        "vx_overview.adomain": "%draftkings%",
    }

    raw = run_looker_query(
        token,
        "vx_analytics",
        "vx_overview",
        fields,
        filters,
        sorts=[f"{LOOKER_FIELD_SPEND} desc"],
        limit=PAGE_SIZE,
    )
    if not raw:
        raise RuntimeError("No DraftKings spend rows returned from vx_overview")

    df = looker_rows_to_df(
        raw,
        {
            LOOKER_FIELD_ADOMAIN: "domain",
            LOOKER_FIELD_RTB: "rtb_account_id",
            LOOKER_FIELD_GEO: "country",
            LOOKER_FIELD_SPEND: "network_spend_7d",
        },
    )
    df["network_spend_7d"] = pd.to_numeric(df["network_spend_7d"], errors="coerce").fillna(0)
    for col in ["domain", "rtb_account_id", "country"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["domain"] = df["domain"].str.lower()
    df = df[
        df["domain"].str.contains(DK_DOMAIN_HINT, na=False)
        & df["rtb_account_id"].ne("")
        & df["country"].ne("")
    ].copy()

    grouped = (
        df.groupby(["domain", "rtb_account_id", "country"], as_index=False)
        .agg(network_spend_7d=("network_spend_7d", "sum"))
        .sort_values("network_spend_7d", ascending=False)
        .reset_index(drop=True)
    )
    grouped["network_daily_spend"] = (grouped["network_spend_7d"] / LOOKBACK_DAYS).round(4)
    grouped.to_csv(CACHE_COMBOS_RAW, index=False)
    log(f"  Raw DraftKings combos: {len(grouped):,} rows → {CACHE_COMBOS_RAW}")

    top = grouped[grouped["network_daily_spend"] >= COMBO_MIN_DAILY_SPEND].copy()
    top = top.sort_values("network_spend_7d", ascending=False).reset_index(drop=True)
    if top.empty:
        raise RuntimeError(f"No DraftKings combos found with ≥ ${COMBO_MIN_DAILY_SPEND:,}/day spend")
    top["combo_id"] = [f"Combo{i + 1}" for i in range(len(top))]
    top = top[
        ["combo_id", "domain", "rtb_account_id", "country", "network_spend_7d", "network_daily_spend"]
    ]
    top.to_csv(COMBOS_CSV, index=False)
    log(f"  Saved {len(top)} combos (≥ ${COMBO_MIN_DAILY_SPEND:,}/day) → {COMBOS_CSV}")

    print("\nTop DraftKings network combos (7d spend):")
    print(
        top[["combo_id", "domain", "rtb_account_id", "country", "network_spend_7d", "network_daily_spend"]]
        .to_string(index=False)
    )

    out = _read_combos_csv(COMBOS_CSV)
    return out


def load_network_total():
    for path in [NETWORK_TOTAL_CACHE, os.path.join(OUTPUT_DIR, "p3_network_total.csv")]:
        if os.path.exists(path):
            total = float(pd.read_csv(path)["total_daily_spend"].iloc[0])
            log(f"Network daily spend: ${total:,.2f} (from {path})")
            return total
    raise FileNotFoundError(
        "Missing network total cache. Run audit_pub_blocked_domains.py step 3 first."
    )


def get_looker_token():
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}",
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_supply(token=None):
    cached = CACHE_APPS
    if os.path.exists(cached) and "--refresh-supply" not in sys.argv:
        df = pd.read_csv(cached, dtype=str)
        df["daily_spend"] = pd.to_numeric(df["daily_spend"], errors="coerce").fillna(0)
        log(f"Supply: {len(df):,} apps from cache")
        return df

    for path in [SUPPLY_CACHE, DOMAIN_SUPPLY_CACHE]:
        if os.path.exists(path):
            df = pd.read_csv(path, dtype=str)
            df["daily_spend"] = pd.to_numeric(df["daily_spend"], errors="coerce").fillna(0)
            df.to_csv(cached, index=False)
            log(f"Supply: {len(df):,} apps (from {path})")
            return df

    if not token:
        token = get_looker_token()

    fields = [
        "publisher_accounts.id",
        "publisher_accounts.name",
        "publisher_apps.id",
        "publisher_apps.name",
        "publisher_apps.market_id",
        "salesforce_accounts_monetize.am_user_region",
        "salesforce_accounts_monetize.am_user_name",
        "publisher_report.unified_ad_spend",
    ]
    filters = {
        "publisher_report.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
        "publisher_report.unified_ad_spend": f">={TOTAL_THRESHOLD}",
    }
    log("Fetching qualifying publisher apps from Looker...")
    payload = {
        "model": "dmx_reports",
        "view": "publisher_report",
        "fields": fields,
        "filters": filters,
        "sorts": ["publisher_report.unified_ad_spend desc"],
        "limit": str(PAGE_SIZE),
    }
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=600,
    )
    resp.raise_for_status()
    raw = resp.json()
    raw_df = pd.DataFrame(raw)
    raw_df.columns = [
        "account_id", "account_name", "app_id", "app_name", "pub_market_id",
        "region", "am_name", "total_spend_7d",
    ]
    for col in ["account_id", "app_id", "account_name", "app_name", "pub_market_id", "region", "am_name"]:
        raw_df[col] = raw_df[col].fillna("").astype(str).str.strip()
    raw_df["total_spend_7d"] = pd.to_numeric(raw_df["total_spend_7d"], errors="coerce").fillna(0)
    raw_df = (
        raw_df.groupby(
            ["account_id", "account_name", "app_id", "app_name", "pub_market_id", "region", "am_name"],
            dropna=False,
        )
        .agg(total_spend_7d=("total_spend_7d", "sum"))
        .reset_index()
    )
    raw_df["daily_spend"] = (raw_df["total_spend_7d"] / LOOKBACK_DAYS).round(4)
    raw_df = raw_df[raw_df["daily_spend"] >= SPEND_THRESHOLD].copy()
    raw_df.to_csv(cached, index=False)
    log(f"Supply: {len(raw_df):,} qualifying apps")
    return raw_df


def fetch_app_exemptions(app_id):
    try:
        resp = requests.post(
            DASHBOARD_API_URL,
            headers=DASHBOARD_HEADERS,
            json={
                "operationName": "application",
                "variables": {"id": str(app_id).strip()},
                "query": APP_QUERY,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        app = resp.json().get("data", {}).get("application")
        if not app:
            return None
        return {
            "app_id": str(app_id).strip(),
            "account_id": str(app.get("owner") or "").strip(),
            "cat_blocklist": app.get("adCatBlocklist") or [],
            "exemptions": app.get("blocklistExemptions") or [],
        }
    except Exception:
        return None


def fetch_all_exemptions(apps_df):
    if os.path.exists(CACHE_DONE) and os.path.exists(CACHE_EXEMPTIONS) and "--refresh-api" not in sys.argv:
        df = pd.read_csv(CACHE_EXEMPTIONS, dtype=str)
        df["exemptions"] = df["exemptions"].apply(parse_exemptions)
        df["cat_blocklist"] = df["cat_blocklist"].apply(parse_exemptions)
        log(f"Exemptions cache: {len(df):,} apps")
        return df

    already_done = set()
    rows = []
    if os.path.exists(CACHE_EXEMPTIONS):
        partial = pd.read_csv(CACHE_EXEMPTIONS, dtype=str)
        if not partial.empty:
            rows = partial.to_dict("records")
    if os.path.exists(CACHE_FETCHED):
        already_done = set(pd.read_csv(CACHE_FETCHED, dtype=str)["app_id"].astype(str).str.strip())

    app_ids = apps_df["app_id"].astype(str).str.strip().tolist()
    remaining = [a for a in app_ids if a not in already_done]
    total = len(app_ids)
    log(f"Fetching blocklistExemptions for {len(remaining):,} remaining of {total:,} apps...")

    def flush():
        out = pd.DataFrame(rows)
        if not out.empty:
            out["exemptions"] = out["exemptions"].apply(
                lambda x: json.dumps(x) if isinstance(x, list) else x
            )
            out["cat_blocklist"] = out["cat_blocklist"].apply(
                lambda x: json.dumps(x) if isinstance(x, list) else x
            )
            out.to_csv(CACHE_EXEMPTIONS, index=False)
        pd.DataFrame({"app_id": sorted(already_done | set(remaining))}).to_csv(CACHE_FETCHED, index=False)

    completed = len(already_done)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_app_exemptions, app_id): app_id for app_id in remaining}
        for i, future in enumerate(as_completed(futures), 1):
            app_id = futures[future]
            already_done.add(app_id)
            result = future.result()
            if result:
                rows.append(result)
            completed += 1
            if i % 100 == 0 or i == len(remaining):
                log(f"  Progress: {completed}/{total} apps...")
                flush()

    flush()
    with open(CACHE_DONE, "w") as f:
        f.write("done\n")

    df = pd.read_csv(CACHE_EXEMPTIONS, dtype=str)
    df["exemptions"] = df["exemptions"].apply(parse_exemptions)
    df["cat_blocklist"] = df["cat_blocklist"].apply(parse_exemptions)
    log(f"Fetched exemptions for {len(df):,} apps")
    return df


def is_draftkings_exemption(exemption, combo_domains):
    if str(exemption.get("advCatId", "")).strip().upper() != GAMBLING_CAT:
        return False
    domain = norm_domain(exemption.get("domain"))
    if not domain:
        return False
    if DK_DOMAIN_HINT in domain:
        return True
    return domain in combo_domains


def is_draftkings_exemption_row(exemption, combo_domains):
    if str(exemption.get("advCatId", "")).strip().upper() != GAMBLING_CAT:
        return False
    domain = norm_domain(exemption.get("domain"))
    if not domain:
        return False
    return DK_DOMAIN_HINT in domain or domain in combo_domains


def combo_is_covered(exemptions, combo, combo_domains):
    """Blank country on an exemption means all geos for that domain+RTB."""
    target_domain = norm_domain(combo["domain"])
    target_rtb = norm_rtb(combo["rtb_account_id"])
    target_country = norm_country(combo["country"])

    for ex in exemptions:
        if not is_draftkings_exemption_row(ex, combo_domains):
            continue
        if norm_domain(ex.get("domain")) != target_domain:
            continue
        if norm_rtb(ex.get("rtbAccountId")) != target_rtb:
            continue
        ex_country = norm_country(ex.get("country"))
        if ex_country == "" or ex_country == target_country:
            return True
    return False


def has_draftkings_exemption(exemptions, combo_domains):
    return any(is_draftkings_exemption(ex, combo_domains) for ex in exemptions)


def count_covered_combos(exemptions, expected, combo_domains):
    return sum(
        1
        for cid in expected.index
        if combo_is_covered(exemptions, expected.loc[cid], combo_domains)
    )


def missing_combo_ids(exemptions, expected, combo_domains):
    return [
        cid
        for cid in expected.index
        if not combo_is_covered(exemptions, expected.loc[cid], combo_domains)
    ]


def collect_qualifying_apps(exemptions_df, combos_df):
    combo_domains = {norm_domain(d) for d in combos_df["domain"] if norm_domain(d)}
    expected = combos_df.set_index("combo_id", drop=False)
    qualifying = []

    for _, row in exemptions_df.iterrows():
        app_id = str(row["app_id"]).strip()
        exemptions = row.get("exemptions") or []
        if not has_draftkings_exemption(exemptions, combo_domains):
            continue
        missing_ids = missing_combo_ids(exemptions, expected, combo_domains)
        if not missing_ids:
            continue
        covered_ids = [cid for cid in expected.index if cid not in missing_ids]
        qualifying.append(
            {
                "app_id": app_id,
                "exemptions": exemptions,
                "exempted_combo_count": len(covered_ids),
                "missing_combo_count": len(missing_ids),
                "covered_ids": covered_ids,
                "missing_ids": missing_ids,
            }
        )
    return qualifying, expected, combo_domains


def fetch_app_combo_spend(token, app_ids):
    if (
        os.path.exists(CACHE_APP_COMBO_SPEND)
        and "--refresh-app-spend" not in sys.argv
    ):
        df = pd.read_csv(CACHE_APP_COMBO_SPEND, dtype=str)
        df["daily_spend"] = pd.to_numeric(df["daily_spend"], errors="coerce").fillna(0)
        log(f"App combo spend cache: {len(df):,} rows")
        return df

    if not app_ids:
        return pd.DataFrame(
            columns=["app_id", "domain", "rtb_account_id", "country", "daily_spend"]
        )

    date_filter = f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
    fields = [
        "publisher_apps.id",
        LOOKER_FIELD_ADOMAIN,
        LOOKER_FIELD_RTB,
        LOOKER_FIELD_GEO,
        LOOKER_FIELD_SPEND,
    ]
    app_ids = [str(a).strip() for a in app_ids]
    batches = [app_ids[i : i + APP_BATCH] for i in range(0, len(app_ids), APP_BATCH)]
    log(f"Fetching app × DK combo spend from Looker ({len(app_ids)} apps)...", "STEP")

    all_rows = []
    for idx, batch in enumerate(batches, 1):
        raw = run_looker_query(
            token,
            "vx_analytics",
            "vx_overview",
            fields,
            {
                "vx_overview.event_date": date_filter,
                "publisher_apps.id": ",".join(batch),
                "vx_overview.adomain": "%draftkings%",
            },
            sorts=[f"{LOOKER_FIELD_SPEND} desc"],
            limit=PAGE_SIZE,
        )
        all_rows.extend(raw or [])
        log(f"  Batch {idx}/{len(batches)}: {len(all_rows):,} rows so far")

    if not all_rows:
        df = pd.DataFrame(
            columns=["app_id", "domain", "rtb_account_id", "country", "daily_spend"]
        )
    else:
        df = looker_rows_to_df(
            all_rows,
            {
                "publisher_apps.id": "app_id",
                LOOKER_FIELD_ADOMAIN: "domain",
                LOOKER_FIELD_RTB: "rtb_account_id",
                LOOKER_FIELD_GEO: "country",
                LOOKER_FIELD_SPEND: "total_spend_7d",
            },
        )
        df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
        for col in ["app_id", "domain", "rtb_account_id", "country"]:
            df[col] = df[col].fillna("").astype(str).str.strip()
        df["domain"] = df["domain"].str.lower()
        df = df.groupby(["app_id", "domain", "rtb_account_id", "country"], as_index=False).agg(
            total_spend_7d=("total_spend_7d", "sum")
        )
        df["daily_spend"] = (df["total_spend_7d"] / LOOKBACK_DAYS).round(4)
        df = df[["app_id", "domain", "rtb_account_id", "country", "daily_spend"]]

    df.to_csv(CACHE_APP_COMBO_SPEND, index=False)
    log(f"  Saved app combo spend → {CACHE_APP_COMBO_SPEND} ({len(df):,} rows)")
    return df


def attach_combo_ids(spend_df, combos_df):
    if spend_df.empty:
        return spend_df
    combo_lookup = {}
    for _, combo in combos_df.iterrows():
        combo_lookup[combo["combo_key"]] = combo["combo_id"]
    spend_df = spend_df.copy()
    spend_df["combo_key"] = spend_df.apply(
        lambda r: combo_key(r["domain"], r["rtb_account_id"], r["country"]), axis=1
    )
    spend_df["combo_id"] = spend_df["combo_key"].map(combo_lookup)
    return spend_df


def build_app_summary(apps_df, qualifying, expected, combos_df, app_spend_df):
    log("Building app-level summary (penetration-based uplift)...", "STEP")
    app_meta = apps_df.set_index("app_id", drop=False)
    app_meta.index = app_meta.index.astype(str).str.strip()

    spend_with_combo = attach_combo_ids(app_spend_df, combos_df)
    total_combo_network_daily = float(combos_df["network_daily_spend"].sum())

    rows = []
    for item in qualifying:
        app_id = item["app_id"]
        if app_id not in app_meta.index:
            continue
        meta = app_meta.loc[app_id]
        covered_ids = item["covered_ids"]
        missing_ids = item["missing_ids"]

        covered_keys = {expected.loc[cid, "combo_key"] for cid in covered_ids}
        app_spend = spend_with_combo[
            (spend_with_combo["app_id"] == app_id)
            & (spend_with_combo["combo_key"].isin(covered_keys))
        ]
        dk_app_daily_spend_exempted = float(app_spend["daily_spend"].sum()) if not app_spend.empty else 0.0

        exempted_network_daily = float(
            combos_df.loc[combos_df["combo_id"].isin(covered_ids), "network_daily_spend"].sum()
        )
        missing_network_daily = float(
            combos_df.loc[combos_df["combo_id"].isin(missing_ids), "network_daily_spend"].sum()
        )

        penetration = (
            dk_app_daily_spend_exempted / total_combo_network_daily
            if total_combo_network_daily > 0
            else 0
        )
        est_uplift_missed = round(penetration * missing_network_daily, 4)

        rows.append(
            {
                "pub_account": meta.get("account_name", ""),
                "pub_account_id": meta.get("account_id", ""),
                "region": meta.get("region", ""),
                "am_name": meta.get("am_name", ""),
                "pub_app": meta.get("app_name", ""),
                "pub_app_id": app_id,
                "pub_market_id": meta.get("pub_market_id", ""),
                "pub_daily_spend": round(float(meta.get("daily_spend", 0) or 0), 4),
                "total_combos": len(expected),
                "exempted_combo_count": item["exempted_combo_count"],
                "missing_combo_count": item["missing_combo_count"],
                "missing_combos": ",".join(missing_ids),
                "dk_app_daily_spend_exempted": round(dk_app_daily_spend_exempted, 4),
                "exempted_combo_network_daily": round(exempted_network_daily, 4),
                "missing_combo_network_daily": round(missing_network_daily, 4),
                "dk_combo_network_daily_total": round(total_combo_network_daily, 4),
                "penetration_rate": round(penetration, 8),
                "est_uplift_missed_daily": est_uplift_missed,
            }
        )

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("est_uplift_missed_daily", ascending=False).reset_index(drop=True)
    out.to_csv(OUTPUT_FILE_APP, index=False)
    log(f"App summary → {OUTPUT_FILE_APP} ({len(out):,} rows)")
    return out


def build_audit(apps_df, exemptions_df, combos_df, network_total, qualifying, expected, combo_domains):
    log("Building missing-combo audit (combo-level)...", "STEP")
    log("  Blank exemption country = all geos for that domain+RTB", "STEP")

    app_meta = apps_df.set_index("app_id", drop=False)
    app_meta.index = app_meta.index.astype(str).str.strip()

    log(f"  Apps with DraftKings exemption but incomplete combos: {len(qualifying):,}")

    rows = []
    for item in qualifying:
        app_id = item["app_id"]
        if app_id not in app_meta.index:
            continue
        meta = app_meta.loc[app_id]
        pub_daily = float(meta.get("daily_spend", 0) or 0)
        exempted_count = item["exempted_combo_count"]
        missing_ids = item["missing_ids"]

        for combo_id in missing_ids:
            combo = expected.loc[combo_id]
            combo_daily = float(combo["network_daily_spend"])
            network_share = combo_daily / network_total if network_total > 0 else 0
            est_uplift = round(network_share * pub_daily, 4)

            rows.append(
                {
                    "pub_account": meta.get("account_name", ""),
                    "pub_account_id": meta.get("account_id", ""),
                    "region": meta.get("region", ""),
                    "am_name": meta.get("am_name", ""),
                    "pub_app": meta.get("app_name", ""),
                    "pub_app_id": app_id,
                    "pub_market_id": meta.get("pub_market_id", ""),
                    "pub_daily_spend": round(pub_daily, 4),
                    "missing_combo": combo_id,
                    "domain": combo["domain"],
                    "rtb_account_id": combo["rtb_account_id"],
                    "country": combo["country"],
                    "combo_daily_spend": round(combo_daily, 4),
                    "network_share": round(network_share, 8),
                    "est_uplift_daily": est_uplift,
                    "exempted_combo_count": exempted_count,
                    "total_combos": len(expected),
                }
            )

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("est_uplift_daily", ascending=False).reset_index(drop=True)
    out.to_csv(OUTPUT_FILE, index=False)
    log(f"Output → {OUTPUT_FILE} ({len(out):,} rows)")
    return out


def clear_caches():
    for path in [
        CACHE_APPS, CACHE_EXEMPTIONS, CACHE_FETCHED, CACHE_DONE,
        CACHE_APP_COMBO_SPEND, OUTPUT_FILE, OUTPUT_FILE_APP,
    ]:
        if os.path.exists(path):
            os.remove(path)
            log(f"  deleted {path}")


def main():
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    if "--refresh" in sys.argv:
        clear_caches()

    if "--fetch-combos" in sys.argv:
        token = get_looker_token()
        fetch_draftkings_combos(token)
        if "--fetch-combos-only" in sys.argv:
            return

    combos_df = load_combos()
    network_total = load_network_total()
    token = get_looker_token()
    apps_df = fetch_supply(token)
    exemptions_df = fetch_all_exemptions(apps_df)

    qualifying, expected, combo_domains = collect_qualifying_apps(exemptions_df, combos_df)
    qualifying_app_ids = [q["app_id"] for q in qualifying]
    app_spend_df = fetch_app_combo_spend(token, qualifying_app_ids)

    output = build_audit(apps_df, exemptions_df, combos_df, network_total, qualifying, expected, combo_domains)
    app_output = build_app_summary(apps_df, qualifying, expected, combos_df, app_spend_df)

    log("═" * 60, "STEP")
    log("AUDIT COMPLETE", "STEP")
    if output.empty:
        log("No missing combo gaps found (or update draftkings_combos.csv spend values)", "WARN")
    else:
        log(f"Combo-level gap rows: {len(output):,}")
        log(f"Unique apps:           {output['pub_app_id'].nunique():,}")
        print("\nTop 10 combo gaps by est_uplift_daily:")
        print(
            output[
                [
                    "pub_account", "pub_app", "missing_combo", "domain",
                    "rtb_account_id", "country", "est_uplift_daily",
                ]
            ]
            .head(10)
            .to_string(index=False)
        )
    if not app_output.empty:
        log(f"App-level summary rows: {len(app_output):,}")
        print("\nTop 10 apps by est_uplift_missed_daily (penetration-based):")
        print(
            app_output[
                [
                    "pub_account", "pub_app", "exempted_combo_count", "missing_combo_count",
                    "dk_app_daily_spend_exempted", "penetration_rate", "est_uplift_missed_daily",
                ]
            ]
            .head(10)
            .to_string(index=False)
        )
    log("Update combos: re-run with --fetch-combos", "STEP")
    log("═" * 60, "STEP")


if __name__ == "__main__":
    main()
