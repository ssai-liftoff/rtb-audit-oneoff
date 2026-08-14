"""
Publisher Category Blocks Audit

Step 1: Qualifying publisher apps + accounts (≥$500/day) from publisher_report
Step 2: Category blocklists from Dashboard API (adCatBlocklist)
Step 3: Network category spend via IAB → V-code mapping from vx_overview
Step 4: Build audit with account vs app rows separated

Account API: adCatBlocklist
App API:     adCatBlocklist minus account list → app-only categories

Uplift = (category_daily_spend / total_network_daily_spend) × pub_daily_spend

Output:
  output/pub_blocked_categories/audit_pub_blocked_categories_combined.csv
"""

import json
import os
import sys
import time

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
IAB_LOOKER_LIMIT = 100_000
API_SLEEP_SEC = 0.05

LOOKER_FIELD_IAB = "vx_overview.content_category_code"
LOOKER_FIELD_SPEND = "vx_overview.unified_ad_spend"

OUTPUT_DIR = "output/pub_blocked_categories"
DOMAIN_SUPPLY_DIR = "output/pub_blocked_domains"
IAB_MAPPING_CSV = os.path.join(os.path.dirname(__file__), "data", "iab_vcode_mapping.csv")
os.makedirs(OUTPUT_DIR, exist_ok=True)

CACHE_FILES = {
    "apps": f"{OUTPUT_DIR}/p1_apps.csv",
    "accounts": f"{OUTPUT_DIR}/p1_accounts.csv",
    "account_blocks": f"{OUTPUT_DIR}/p2_account_blocklists.csv",
    "app_blocks": f"{OUTPUT_DIR}/p2_app_blocklists.csv",
    "network_total": f"{OUTPUT_DIR}/p3_network_total.csv",
    "iab_spend": f"{OUTPUT_DIR}/p3_iab_spend.csv",
    "category_spend": f"{OUTPUT_DIR}/p3_category_spend.csv",
    "final": f"{OUTPUT_DIR}/audit_pub_blocked_categories_combined.csv",
}

ACCOUNT_BLOCKLIST_QUERY = """
query account($id: String!) {
  account(id: $id) {
    id
    adCatBlocklist
  }
}
"""

APP_BLOCKLIST_QUERY = """
query application($id: String!) {
  application(id: $id) {
    id
    owner
    adCatBlocklist
  }
}
"""


def log(msg, level="INFO"):
    from datetime import datetime

    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def load_cache(path):
    if os.path.exists(path):
        log(f"Cache hit — loading {path}")
        return pd.read_csv(path, dtype=str)
    return None


def save_cache(df, path):
    df.to_csv(path, index=False)
    log(f"Saved cache → {path} ({len(df):,} rows)")


def clear_caches():
    log("Clearing all cached files...", "STEP")
    extra = [
        f"{OUTPUT_DIR}/p2_apps_fetched.csv",
        f"{CACHE_FILES['app_blocks']}.done",
    ]
    for path in list(CACHE_FILES.values()) + extra:
        if os.path.exists(path):
            os.remove(path)
            log(f"  deleted {path}")
    legacy = "output/audit_pub_blocked_categories_combined.csv"
    if os.path.exists(legacy):
        os.remove(legacy)


def parse_json_list_cell(val):
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


def serialize_list_column(df, col):
    out = df.copy()
    if col in out.columns:
        out[col] = out[col].apply(
            lambda x: json.dumps(x) if isinstance(x, list) else (x if pd.notna(x) else "[]")
        )
    return out


def deserialize_list_columns(df, cols):
    out = df.copy()
    for col in cols:
        if col in out.columns:
            out[col] = out[col].apply(parse_json_list_cell)
    return out


def normalize_category_code(val):
    return str(val).strip().upper()


def iab_matches(iab_code, allowed_codes):
    if not iab_code or not allowed_codes:
        return False
    iab_code = str(iab_code).strip()
    for allowed in allowed_codes:
        if iab_code == allowed or iab_code.startswith(allowed + "-"):
            return True
    return False


_MAPPING_CACHE = None


def load_iab_vcode_mapping(csv_path=IAB_MAPPING_CSV):
    df = pd.read_csv(csv_path, dtype=str)
    v_code_to_iab = {}
    internal_cat_names = {}

    for _, row in df.iterrows():
        v_code = normalize_category_code(row.get("Internal Code (V codes)", ""))
        iab_code = str(row.get("IAB Code", "")).strip()
        cat_name = str(row.get("Internal Category name (V codes)", "")).strip()

        if v_code and cat_name and v_code not in internal_cat_names:
            internal_cat_names[v_code] = cat_name

        if not v_code or iab_code.lower() in ("n/a", "", "nan"):
            continue
        bucket = v_code_to_iab.setdefault(v_code, [])
        if iab_code not in bucket:
            bucket.append(iab_code)

    return v_code_to_iab, internal_cat_names


def get_mapping():
    global _MAPPING_CACHE
    if _MAPPING_CACHE is None:
        if not os.path.exists(IAB_MAPPING_CSV):
            raise FileNotFoundError(f"Missing IAB mapping CSV: {IAB_MAPPING_CSV}")
        _MAPPING_CACHE = load_iab_vcode_mapping()
        v_code_to_iab, internal_cat_names = _MAPPING_CACHE
        log(f"Loaded IAB mapping: {len(v_code_to_iab)} V-codes, {len(internal_cat_names)} names")
    return _MAPPING_CACHE


def looker_rows_to_df(raw, rename_map):
    if not raw:
        return pd.DataFrame(columns=list(rename_map.values()))
    df = pd.DataFrame(raw)
    missing = [src for src in rename_map if src not in df.columns]
    if missing:
        raise RuntimeError(f"Looker response missing expected fields: {missing}")
    return df.rename(columns=rename_map)


def safe_lookup(df, key_col, value_cols):
    if df is None or df.empty:
        return {}
    cols = [key_col] + [c for c in value_cols if c in df.columns]
    work = df[cols].copy()
    work[key_col] = work[key_col].astype(str).str.strip()
    work = work[work[key_col].notna() & (work[key_col] != "") & (work[key_col] != "nan")]
    agg = {}
    for c in value_cols:
        if c not in work.columns:
            continue
        if "spend" in c.lower() or "share" in c.lower():
            work[c] = pd.to_numeric(work[c], errors="coerce").fillna(0)
            agg[c] = "sum"
        else:
            agg[c] = "first"
    work = work.groupby(key_col, as_index=False).agg(agg)
    return work.set_index(key_col)[list(agg.keys())].to_dict("index")


# ── Looker ────────────────────────────────────────────────────────────────────

def get_looker_token():
    log("Authenticating with Looker...")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}",
    )
    resp.raise_for_status()
    log("Authenticated")
    return resp.json()["access_token"]


def looker_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def run_query(token, model, view, fields, filters, sorts=None, limit=PAGE_SIZE, offset=0, retries=2):
    payload = {
        "model": model,
        "view": view,
        "fields": fields,
        "filters": filters,
        "limit": str(limit),
        "offset": str(offset),
    }
    if sorts:
        payload["sorts"] = sorts
    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.post(
                f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
                headers=looker_headers(token),
                json=payload,
                timeout=600,
            )
            if not resp.ok:
                log(f"Looker error {resp.status_code}: {resp.text[:500]}", "ERROR")
                resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            last_err = e
            if attempt < retries:
                log(f"Looker timeout (attempt {attempt + 1}/{retries + 1}), retrying...", "WARN")
            else:
                raise last_err


def run_paginated(token, model, view, fields, filters, sorts=None, label="query"):
    all_rows = []
    offset = 0
    page = 0
    while True:
        page += 1
        batch = run_query(token, model, view, fields, filters, sorts, PAGE_SIZE, offset)
        all_rows.extend(batch)
        log(f"  {label} page {page}: {len(batch):,} rows (total {len(all_rows):,})")
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return all_rows


# ── Dashboard API ─────────────────────────────────────────────────────────────

def fetch_account_categories_from_api(account_id):
    try:
        resp = requests.post(
            DASHBOARD_API_URL,
            headers=DASHBOARD_HEADERS,
            json={
                "operationName": "account",
                "variables": {"id": str(account_id).strip()},
                "query": ACCOUNT_BLOCKLIST_QUERY,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        account = resp.json().get("data", {}).get("account")
        if not account:
            return None
        cats = sorted(
            {
                normalize_category_code(c)
                for c in (account.get("adCatBlocklist") or [])
                if str(c).strip()
            }
        )
        if not cats:
            return None
        return {"account_id": str(account_id).strip(), "blocked_categories": cats}
    except Exception as e:
        log(f"Error fetching account {account_id}: {e}", "WARN")
        return None


def fetch_app_categories_from_api(app_id, account_cat_set):
    try:
        resp = requests.post(
            DASHBOARD_API_URL,
            headers=DASHBOARD_HEADERS,
            json={
                "operationName": "application",
                "variables": {"id": str(app_id).strip()},
                "query": APP_BLOCKLIST_QUERY,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        app = resp.json().get("data", {}).get("application")
        if not app:
            return None
        app_cats_all = {
            normalize_category_code(c)
            for c in (app.get("adCatBlocklist") or [])
            if str(c).strip()
        }
        app_only = sorted(app_cats_all - account_cat_set)
        if not app_only:
            return None
        return {
            "app_id": str(app_id).strip(),
            "account_id": str(app.get("owner") or "").strip(),
            "app_only_categories": app_only,
        }
    except Exception as e:
        log(f"Error fetching app {app_id}: {e}", "WARN")
        return None


# ── Step 1: Qualifying supply ─────────────────────────────────────────────────

def fetch_supply(token):
    log("STEP 1: Fetch qualifying publisher apps + accounts", "STEP")

    cached_apps = load_cache(CACHE_FILES["apps"])
    cached_accounts = load_cache(CACHE_FILES["accounts"])
    if cached_apps is not None and cached_accounts is not None:
        apps_df = cached_apps.copy()
        accounts_df = cached_accounts.copy()
        for df in (apps_df, accounts_df):
            df["daily_spend"] = pd.to_numeric(df["daily_spend"], errors="coerce").fillna(0)
        log(f"  {len(apps_df):,} apps, {len(accounts_df):,} accounts (from cache)")
        return apps_df, accounts_df

    domain_apps = f"{DOMAIN_SUPPLY_DIR}/p1_apps.csv"
    domain_accounts = f"{DOMAIN_SUPPLY_DIR}/p1_accounts.csv"
    if os.path.exists(domain_apps) and os.path.exists(domain_accounts):
        log("  Reusing supply cache from pub_blocked_domains audit")
        apps_df = pd.read_csv(domain_apps, dtype=str)
        accounts_df = pd.read_csv(domain_accounts, dtype=str)
        for df in (apps_df, accounts_df):
            df["daily_spend"] = pd.to_numeric(df["daily_spend"], errors="coerce").fillna(0)
        save_cache(apps_df, CACHE_FILES["apps"])
        save_cache(accounts_df, CACHE_FILES["accounts"])
        log(f"  {len(apps_df):,} apps, {len(accounts_df):,} accounts")
        return apps_df, accounts_df

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
    log(f"Fetching publisher spend (≥${SPEND_THRESHOLD:,}/day over {LOOKBACK_DAYS}d)...")
    raw = run_paginated(
        token,
        "dmx_reports",
        "publisher_report",
        fields,
        filters,
        sorts=["publisher_report.unified_ad_spend desc"],
        label="supply",
    )
    raw_df = pd.DataFrame(raw)
    if raw_df.empty:
        raise RuntimeError("No publisher spend rows returned from publisher_report")

    ncols = len(raw_df.columns)
    if ncols == 8:
        raw_df.columns = [
            "account_id", "account_name", "app_id", "app_name", "pub_market_id",
            "region", "am_name", "total_spend_7d",
        ]
    elif ncols == 6:
        raw_df.columns = [
            "account_id", "account_name", "app_id", "app_name", "pub_market_id", "total_spend_7d",
        ]
        raw_df["region"] = ""
        raw_df["am_name"] = ""
    else:
        raise RuntimeError(f"Unexpected column count from supply query: {ncols}")

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

    apps_df = raw_df.sort_values("daily_spend", ascending=False).reset_index(drop=True)
    save_cache(apps_df, CACHE_FILES["apps"])

    accounts_df = (
        apps_df.groupby(["account_id", "account_name", "region", "am_name"], dropna=False)
        .agg(daily_spend=("daily_spend", "sum"))
        .reset_index()
    )
    accounts_df = accounts_df[accounts_df["daily_spend"] >= SPEND_THRESHOLD].copy()
    accounts_df["daily_spend"] = accounts_df["daily_spend"].round(4)
    accounts_df = accounts_df.sort_values("daily_spend", ascending=False).reset_index(drop=True)
    save_cache(accounts_df, CACHE_FILES["accounts"])
    log(f"  {len(apps_df):,} qualifying apps, {len(accounts_df):,} qualifying accounts")
    return apps_df, accounts_df


# ── Step 2: Category blocklists (Dashboard API) ───────────────────────────────

def fetch_account_blocklists(accounts_df):
    log("STEP 2a: Fetch account-level category blocklists (Dashboard API)", "STEP")
    cached = load_cache(CACHE_FILES["account_blocks"])
    if cached is not None:
        df = deserialize_list_columns(cached, ["blocked_categories"])
        log(f"  {len(df):,} accounts with category blocks (from cache)")
        return df

    total = len(accounts_df)
    log(f"Fetching adCatBlocklist for {total:,} qualifying accounts...")
    rows = []
    for i, (_, row) in enumerate(accounts_df.iterrows(), 1):
        account_id = str(row["account_id"]).strip()
        if i % 50 == 0 or i == total:
            log(f"  Progress: {i}/{total} accounts...")
        result = fetch_account_categories_from_api(account_id)
        if result:
            rows.append(result)
        time.sleep(API_SLEEP_SEC)

    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["account_id", "blocked_categories"])
    if not df.empty:
        df["account_id"] = df["account_id"].astype(str).str.strip()
        df = df.drop_duplicates(subset=["account_id"], keep="first")
    save_cache(serialize_list_column(df, "blocked_categories"), CACHE_FILES["account_blocks"])
    log(f"  {len(df):,} accounts with category blocks")
    return deserialize_list_columns(df, ["blocked_categories"])


def build_account_cat_index(account_blocks_df):
    cats_by_account = {}
    for _, row in account_blocks_df.iterrows():
        account_id = str(row["account_id"]).strip()
        cats_by_account[account_id] = set(row.get("blocked_categories") or [])
    return cats_by_account


def fetch_app_blocklists(apps_df, account_blocks_df):
    log("STEP 2b: Fetch app-level category blocklists (Dashboard API)", "STEP")
    cache_path = CACHE_FILES["app_blocks"]
    fetched_path = f"{OUTPUT_DIR}/p2_apps_fetched.csv"
    done_marker = f"{cache_path}.done"

    if os.path.exists(done_marker) and os.path.exists(cache_path):
        cached = load_cache(cache_path)
        df = deserialize_list_columns(cached, ["app_only_categories"])
        log(f"  {len(df):,} apps with app-only category blocks (from cache)")
        return df

    cats_by_account = build_account_cat_index(account_blocks_df)
    blocklists = []
    already_done = set()

    if os.path.exists(cache_path):
        partial = deserialize_list_columns(
            pd.read_csv(cache_path, dtype=str), ["app_only_categories"]
        )
        if not partial.empty and "app_id" in partial.columns:
            blocklists = partial.to_dict("records")

    if os.path.exists(fetched_path):
        fetched_df = pd.read_csv(fetched_path, dtype=str)
        if "app_id" in fetched_df.columns:
            already_done = set(fetched_df["app_id"].astype(str).str.strip())
            log(f"  Resuming — {len(already_done)} apps already fetched")

    remaining = apps_df[~apps_df["app_id"].astype(str).str.strip().isin(already_done)]
    total = len(apps_df)
    log(f"Processing {len(remaining):,} remaining of {total:,} apps...")

    for i, (_, row) in enumerate(remaining.iterrows(), 1):
        app_id = str(row["app_id"]).strip()
        account_id = str(row["account_id"]).strip()
        result = fetch_app_categories_from_api(app_id, cats_by_account.get(account_id, set()))
        already_done.add(app_id)
        done_count = len(already_done)
        if i % 100 == 0 or i == len(remaining):
            log(f"  Progress: {done_count}/{total} apps...")
        if result:
            blocklists.append(result)
        if i % 100 == 0:
            partial_df = pd.DataFrame(blocklists)
            if not partial_df.empty:
                save_cache(
                    serialize_list_column(partial_df, "app_only_categories"),
                    cache_path,
                )
            pd.DataFrame({"app_id": sorted(already_done)}).to_csv(fetched_path, index=False)
        time.sleep(API_SLEEP_SEC)

    df = pd.DataFrame(blocklists) if blocklists else pd.DataFrame(
        columns=["app_id", "account_id", "app_only_categories"]
    )
    if not df.empty:
        df["app_id"] = df["app_id"].astype(str).str.strip()
        df["account_id"] = df["account_id"].astype(str).str.strip()
        df = df.drop_duplicates(subset=["app_id"], keep="first")
    save_cache(serialize_list_column(df, "app_only_categories"), cache_path)
    pd.DataFrame({"app_id": sorted(already_done)}).to_csv(fetched_path, index=False)
    with open(done_marker, "w") as f:
        f.write("done\n")
    log(f"  {len(df):,} apps with app-only category blocks")
    return deserialize_list_columns(df, ["app_only_categories"])


# ── Step 3: Network category spend ────────────────────────────────────────────

def fetch_network_total(token):
    domain_total = f"{DOMAIN_SUPPLY_DIR}/p3_network_total.csv"
    if os.path.exists(domain_total) and not os.path.exists(CACHE_FILES["network_total"]):
        pd.read_csv(domain_total).to_csv(CACHE_FILES["network_total"], index=False)

    cached = load_cache(CACHE_FILES["network_total"])
    if cached is not None:
        total_daily = float(cached["total_daily_spend"].iloc[0])
        log(f"  Total network daily spend: ${total_daily:,.2f} (from cache)")
        return total_daily

    raw = run_query(
        token,
        "vx_analytics",
        "vx_overview",
        [LOOKER_FIELD_SPEND],
        {"vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"},
        limit=1,
    )
    total_7d = pd.to_numeric(raw[0][LOOKER_FIELD_SPEND], errors="coerce")
    total_daily = float(total_7d / LOOKBACK_DAYS)
    save_cache(
        pd.DataFrame([{"total_spend_7d": round(total_7d, 2), "total_daily_spend": round(total_daily, 4)}]),
        CACHE_FILES["network_total"],
    )
    log(f"  Total network daily spend: ${total_daily:,.2f}")
    return total_daily


def fetch_iab_spend(token):
    cached = load_cache(CACHE_FILES["iab_spend"])
    if cached is not None:
        out = cached.copy()
        out["daily_spend"] = pd.to_numeric(out["daily_spend"], errors="coerce").fillna(0)
        log(f"  {len(out):,} IAB category rows (from cache)")
        return out

    log(f"  IAB spend: global top rows (≥${SPEND_THRESHOLD:,}/day, limit {IAB_LOOKER_LIMIT:,})...")
    raw = run_query(
        token,
        "vx_analytics",
        "vx_overview",
        [LOOKER_FIELD_IAB, LOOKER_FIELD_SPEND],
        {
            "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
            "vx_overview.unified_ad_spend": f">={TOTAL_THRESHOLD}",
        },
        sorts=[f"{LOOKER_FIELD_SPEND} desc"],
        limit=IAB_LOOKER_LIMIT,
    )
    if not raw:
        return pd.DataFrame(columns=["iab_code", "daily_spend"])

    df = looker_rows_to_df(raw, {LOOKER_FIELD_IAB: "iab_code", LOOKER_FIELD_SPEND: "total_spend_7d"})
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["iab_code"] = df["iab_code"].fillna("").astype(str).str.strip()
    df = df[df["iab_code"] != ""].copy()
    df = (
        df.groupby("iab_code", as_index=False)
        .agg(total_spend_7d=("total_spend_7d", "sum"))
    )
    df["daily_spend"] = (df["total_spend_7d"] / LOOKBACK_DAYS).round(4)
    save_cache(df[["iab_code", "daily_spend"]], CACHE_FILES["iab_spend"])
    log(f"  {len(df):,} IAB codes with spend data")
    return df


def build_category_spend_lookup(iab_df):
    v_code_to_iab, internal_cat_names = get_mapping()

    cached = load_cache(CACHE_FILES["category_spend"])
    if cached is not None and "--rebuild-demand" not in sys.argv:
        out = cached.copy()
        out["daily_spend"] = pd.to_numeric(out["daily_spend"], errors="coerce").fillna(0)
        log(f"  {len(out):,} high-spend V-codes (from cache)")
        return out

    spend_by_vcode = {}
    for v_code, allowed in v_code_to_iab.items():
        if not allowed:
            continue
        daily = 0.0
        for _, row in iab_df.iterrows():
            if iab_matches(row["iab_code"], allowed):
                daily += float(row["daily_spend"])
        if daily >= SPEND_THRESHOLD:
            spend_by_vcode[v_code] = round(daily, 4)

    rows = [
        {
            "block_value": v_code,
            "category_name": internal_cat_names.get(v_code, ""),
            "daily_spend": daily,
        }
        for v_code, daily in sorted(spend_by_vcode.items(), key=lambda x: -x[1])
    ]
    df = pd.DataFrame(rows)
    save_cache(df, CACHE_FILES["category_spend"])
    log(f"  {len(df):,} internal category codes ≥ ${SPEND_THRESHOLD:,}/day")
    return df


def fetch_demand_spend(token):
    log("STEP 3: Fetch network category spend (vx_overview → V-codes)", "STEP")
    total_daily = fetch_network_total(token)
    iab_df = fetch_iab_spend(token)
    category_df = build_category_spend_lookup(iab_df)
    return total_daily, category_df


# ── Step 4: Build audit ───────────────────────────────────────────────────────

def build_audit(apps_df, accounts_df, account_blocks_df, app_blocks_df, total_network_daily, category_df):
    log("STEP 4: Build combined audit", "STEP")
    _, internal_cat_names = get_mapping()

    category_lookup = safe_lookup(
        category_df, "block_value", ["category_name", "daily_spend"]
    )
    account_meta = safe_lookup(
        accounts_df, "account_id", ["account_name", "region", "am_name", "daily_spend"]
    )
    app_meta = safe_lookup(
        apps_df,
        "app_id",
        ["account_id", "account_name", "app_name", "pub_market_id", "region", "am_name", "daily_spend"],
    )
    qualifying_account_ids = set(accounts_df["account_id"].astype(str).str.strip())
    rows = []

    def append_row(*, account_id, level, block_value, app_id="", app_name="", pub_market_id=""):
        info = category_lookup.get(block_value, {})
        category_daily = float(info.get("daily_spend", 0) or 0)
        if category_daily < SPEND_THRESHOLD:
            return

        meta = account_meta.get(account_id, {})
        account_name = meta.get("account_name", "")
        region = meta.get("region", "")
        am_name = meta.get("am_name", "")

        if level == "account":
            pub_daily_spend = float(meta.get("daily_spend", 0) or 0)
        else:
            app_info = app_meta.get(app_id, {})
            account_name = app_info.get("account_name", account_name)
            region = app_info.get("region", region)
            am_name = app_info.get("am_name", am_name)
            pub_daily_spend = float(app_info.get("daily_spend", 0) or 0)

        network_share = category_daily / total_network_daily if total_network_daily > 0 else 0
        est_uplift = round(network_share * pub_daily_spend, 4)

        rows.append(
            {
                "pub_account": account_name,
                "pub_account_id": account_id,
                "region": region,
                "am_name": am_name,
                "pub_app": app_name,
                "pub_app_id": app_id,
                "pub_market_id": pub_market_id,
                "pub_daily_spend": round(pub_daily_spend, 4),
                "level": level,
                "block_type": "category",
                "block_value": block_value,
                "category_name": info.get("category_name", internal_cat_names.get(block_value, "")),
                "network_share": round(network_share, 8),
                "category_daily_spend": round(category_daily, 4),
                "est_uplift_daily": est_uplift,
            }
        )

    log("  Processing account-level category blocks...")
    acct_before = len(rows)
    if not account_blocks_df.empty:
        for _, row in account_blocks_df.iterrows():
            account_id = str(row["account_id"]).strip()
            if account_id not in qualifying_account_ids:
                continue
            for cat in row.get("blocked_categories") or []:
                append_row(account_id=account_id, level="account", block_value=normalize_category_code(cat))
    log(f"    Account-level opportunities: {len(rows) - acct_before:,}")

    log("  Processing app-level category blocks (app-only lists)...")
    app_before = len(rows)
    if not app_blocks_df.empty:
        for _, row in app_blocks_df.iterrows():
            app_id = str(row["app_id"]).strip()
            account_id = str(row["account_id"]).strip()
            if app_id not in app_meta:
                continue
            app_info = app_meta[app_id]
            for cat in row.get("app_only_categories") or []:
                append_row(
                    account_id=account_id,
                    level="app",
                    block_value=normalize_category_code(cat),
                    app_id=app_id,
                    app_name=app_info.get("app_name", ""),
                    pub_market_id=app_info.get("pub_market_id", ""),
                )
    log(f"    App-level opportunities: {len(rows) - app_before:,}")

    output = pd.DataFrame(rows)
    if not output.empty:
        output = output.sort_values("est_uplift_daily", ascending=False).reset_index(drop=True)

    save_cache(output, CACHE_FILES["final"])
    output.to_csv("output/audit_pub_blocked_categories_combined.csv", index=False)
    log(f"Combined audit → {CACHE_FILES['final']} ({len(output):,} rows)")
    return output


def main():
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    if "--refresh" in sys.argv:
        clear_caches()
    elif "--rebuild-demand" in sys.argv:
        for path in [CACHE_FILES["category_spend"], CACHE_FILES["final"]]:
            if os.path.exists(path):
                os.remove(path)
                log(f"  deleted {path}")

    get_mapping()

    token = get_looker_token()
    apps_df, accounts_df = fetch_supply(token)
    account_blocks_df = fetch_account_blocklists(accounts_df)
    app_blocks_df = fetch_app_blocklists(apps_df, account_blocks_df)
    total_network_daily, category_df = fetch_demand_spend(token)
    output = build_audit(
        apps_df,
        accounts_df,
        account_blocks_df,
        app_blocks_df,
        total_network_daily,
        category_df,
    )

    log("═" * 60, "STEP")
    log("AUDIT COMPLETE", "STEP")
    if not output.empty:
        log(f"Total opportunities: {len(output):,}")
        log(f"  Account-level: {(output['level'] == 'account').sum():,}")
        log(f"  App-level:     {(output['level'] == 'app').sum():,}")
        print("\nTop 10 by est_uplift_daily:")
        print(
            output[
                [
                    "pub_account", "pub_app", "level", "block_value", "category_name",
                    "category_daily_spend", "est_uplift_daily",
                ]
            ]
            .head(10)
            .to_string(index=False)
        )
    else:
        log("No matching opportunities found", "WARN")
    log("Re-run skips completed steps. Force refresh with: --refresh", "STEP")
    log("═" * 60, "STEP")


if __name__ == "__main__":
    main()
