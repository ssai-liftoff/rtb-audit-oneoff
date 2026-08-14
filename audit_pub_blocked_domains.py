"""
Publisher Domain / Market ID Blocks Audit

Step 1: Qualifying publisher apps + accounts from dmx_reports/publisher_report
Step 2: Blocklists from Dashboard GraphQL API (same token as creative audit)
Step 3: Demand spend from vx_analytics/vx_overview
Step 4: Build audit with account vs app rows separated

Account API fields:
  adDomainBlacklist, adMarketIdBlacklist { ios android }

App API fields:
  adDomainBlacklist minus account list → app-only domains
  marketIdFilters.blacklist minus account markets → app-only market IDs

Uplift = (adv_daily_spend / total_network_daily_spend) × pub_daily_spend

Output:
  output/pub_blocked_domains/audit_pub_blocked_domains_combined.csv
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
    "AwMDAwMCIsImV4cCI6MTc4NTg1OTc0N30.OQUNrOQFk4mrtIVIttoK8rZwjw8zphFXDkU7qt9adTo"
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
ADV_SPEND_LOOKER_LIMIT = 100_000  # same pattern as internal tool — global top spenders, join in step 4
API_SLEEP_SEC = 0.05

# Looker API returns dict keys with full explore field names (same as internal tool)
LOOKER_FIELD_ADOMAIN = "vx_overview.adomain"
LOOKER_FIELD_ADV_BUNDLE = "vx_overview.adv_bundle"
LOOKER_FIELD_ADV_TITLE = "advertiser_app_metadata.title"
LOOKER_FIELD_SPEND = "vx_overview.unified_ad_spend"

OUTPUT_DIR = "output/pub_blocked_domains"
os.makedirs(OUTPUT_DIR, exist_ok=True)

CACHE_FILES = {
    "apps": f"{OUTPUT_DIR}/p1_apps.csv",
    "accounts": f"{OUTPUT_DIR}/p1_accounts.csv",
    "account_blocks": f"{OUTPUT_DIR}/p2_account_blocklists.csv",
    "app_blocks": f"{OUTPUT_DIR}/p2_app_blocklists.csv",
    "network_total": f"{OUTPUT_DIR}/p3_network_total.csv",
    "domain_spend": f"{OUTPUT_DIR}/p3_domain_spend.csv",
    "market_spend": f"{OUTPUT_DIR}/p3_market_spend.csv",
    "final": f"{OUTPUT_DIR}/audit_pub_blocked_domains_combined.csv",
}

ACCOUNT_BLOCKLIST_QUERY = """
query account($id: String!) {
  account(id: $id) {
    id
    adDomainBlacklist
    adMarketIdBlacklist { ios android }
  }
}
"""

APP_BLOCKLIST_QUERY = """
query application($id: String!) {
  application(id: $id) {
    id
    owner
    adDomainBlacklist
    marketIdFilters { blacklist }
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
        f"{OUTPUT_DIR}/p2_blocklists.csv",
    ]
    for path in list(CACHE_FILES.values()) + extra:
        if os.path.exists(path):
            os.remove(path)
            log(f"  deleted {path}")
    legacy = "output/audit_pub_blocked_domains_combined.csv"
    if os.path.exists(legacy):
        os.remove(legacy)
        log(f"  deleted {legacy}")


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


def normalize_domain(val):
    return str(val).strip().lower()


def normalize_market_id(val):
    return str(val).strip()


def looker_rows_to_df(raw, rename_map):
    """Build DataFrame from Looker JSON rows using full field names, not column order."""
    if not raw:
        return pd.DataFrame(columns=list(rename_map.values()))
    df = pd.DataFrame(raw)
    missing = [src for src in rename_map if src not in df.columns]
    if missing:
        raise RuntimeError(f"Looker response missing expected fields: {missing}")
    return df.rename(columns=rename_map)


def flatten_account_markets(market_obj):
    if not market_obj:
        return set()
    out = set()
    for key in ("ios", "android"):
        for x in market_obj.get(key) or []:
            s = normalize_market_id(x)
            if s:
                out.add(s)
    return out


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

    before = len(work)
    work = work.groupby(key_col, as_index=False).agg(agg)
    dupes = before - len(work)
    if dupes > 0:
        log(f"Collapsed {dupes} duplicate '{key_col}' rows before lookup", "WARN")

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

def fetch_account_blocks_from_api(account_id):
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

        domains = [normalize_domain(d) for d in (account.get("adDomainBlacklist") or []) if str(d).strip()]
        markets = sorted(flatten_account_markets(account.get("adMarketIdBlacklist")))
        if not domains and not markets:
            return None

        return {
            "account_id": str(account_id).strip(),
            "blocked_domains": sorted(set(domains)),
            "blocked_market_ids": markets,
        }
    except Exception as e:
        log(f"Error fetching account {account_id}: {e}", "WARN")
        return None


def fetch_app_blocks_from_api(app_id, account_domain_set, account_market_set):
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

        app_domains_all = {
            normalize_domain(d) for d in (app.get("adDomainBlacklist") or []) if str(d).strip()
        }
        app_only_domains = sorted(app_domains_all - account_domain_set)

        app_markets_raw = {
            normalize_market_id(x)
            for x in ((app.get("marketIdFilters") or {}).get("blacklist") or [])
            if str(x).strip()
        }
        app_only_markets = sorted(app_markets_raw - account_market_set)

        if not app_only_domains and not app_only_markets:
            return None

        return {
            "app_id": str(app_id).strip(),
            "account_id": str(app.get("owner") or "").strip(),
            "app_only_domains": app_only_domains,
            "app_only_market_ids": app_only_markets,
        }
    except Exception as e:
        log(f"Error fetching app {app_id}: {e}", "WARN")
        return None


# ── Step 1: Qualifying publisher apps + accounts (publisher_report) ───────────

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
            "account_id",
            "account_name",
            "app_id",
            "app_name",
            "pub_market_id",
            "region",
            "am_name",
            "total_spend_7d",
        ]
    elif ncols == 6:
        raw_df.columns = [
            "account_id",
            "account_name",
            "app_id",
            "app_name",
            "pub_market_id",
            "total_spend_7d",
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


# ── Step 2: Blocklists (Dashboard API) ────────────────────────────────────────

def fetch_account_blocklists(accounts_df):
    log("STEP 2a: Fetch account-level blocklists (Dashboard API)", "STEP")

    cached = load_cache(CACHE_FILES["account_blocks"])
    if cached is not None:
        df = deserialize_list_columns(cached, ["blocked_domains", "blocked_market_ids"])
        log(f"  {len(df):,} accounts with blocks (from cache)")
        return df

    total = len(accounts_df)
    log(f"Fetching blocks for {total:,} qualifying accounts...")
    rows = []

    for i, (_, row) in enumerate(accounts_df.iterrows(), 1):
        account_id = str(row["account_id"]).strip()
        if i % 50 == 0 or i == total:
            log(f"  Progress: {i}/{total} accounts...")
        result = fetch_account_blocks_from_api(account_id)
        if result:
            rows.append(result)
        time.sleep(API_SLEEP_SEC)

    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=["account_id", "blocked_domains", "blocked_market_ids"])
    else:
        df["account_id"] = df["account_id"].astype(str).str.strip()
        df = df.drop_duplicates(subset=["account_id"], keep="first")

    save_cache(
        serialize_list_column(serialize_list_column(df, "blocked_domains"), "blocked_market_ids"),
        CACHE_FILES["account_blocks"],
    )
    log(f"  {len(df):,} accounts with domain and/or market ID blocks")
    return deserialize_list_columns(df, ["blocked_domains", "blocked_market_ids"])


def build_account_block_index(account_blocks_df):
    domains_by_account = {}
    markets_by_account = {}
    for _, row in account_blocks_df.iterrows():
        account_id = str(row["account_id"]).strip()
        domains_by_account[account_id] = set(row.get("blocked_domains") or [])
        markets_by_account[account_id] = set(row.get("blocked_market_ids") or [])
    return domains_by_account, markets_by_account


def fetch_app_blocklists(apps_df, account_blocks_df):
    log("STEP 2b: Fetch app-level blocklists (Dashboard API)", "STEP")

    cache_path = CACHE_FILES["app_blocks"]
    fetched_path = f"{OUTPUT_DIR}/p2_apps_fetched.csv"
    done_marker = f"{cache_path}.done"

    if os.path.exists(done_marker) and os.path.exists(cache_path):
        cached = load_cache(cache_path)
        df = deserialize_list_columns(cached, ["app_only_domains", "app_only_market_ids"])
        log(f"  {len(df):,} apps with app-only blocks (from cache)")
        return df

    domains_by_account, markets_by_account = build_account_block_index(account_blocks_df)

    blocklists = []
    already_done = set()

    if os.path.exists(cache_path):
        partial = deserialize_list_columns(
            pd.read_csv(cache_path, dtype=str), ["app_only_domains", "app_only_market_ids"]
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
        done_count = len(already_done) + i

        if i % 100 == 0 or i == len(remaining):
            log(f"  Progress: {done_count}/{total} apps...")

        acct_domains = domains_by_account.get(account_id, set())
        acct_markets = markets_by_account.get(account_id, set())
        result = fetch_app_blocks_from_api(app_id, acct_domains, acct_markets)
        already_done.add(app_id)

        if result:
            blocklists.append(result)

        if i % 100 == 0:
            partial_df = pd.DataFrame(blocklists)
            if not partial_df.empty:
                save_cache(
                    serialize_list_column(
                        serialize_list_column(partial_df, "app_only_domains"),
                        "app_only_market_ids",
                    ),
                    cache_path,
                )
            pd.DataFrame({"app_id": sorted(already_done)}).to_csv(fetched_path, index=False)

        time.sleep(API_SLEEP_SEC)

    df = pd.DataFrame(blocklists)
    if df.empty:
        df = pd.DataFrame(columns=["app_id", "account_id", "app_only_domains", "app_only_market_ids"])
    else:
        df["app_id"] = df["app_id"].astype(str).str.strip()
        df["account_id"] = df["account_id"].astype(str).str.strip()
        df = df.drop_duplicates(subset=["app_id"], keep="first")

    save_cache(
        serialize_list_column(
            serialize_list_column(df, "app_only_domains"),
            "app_only_market_ids",
        ),
        cache_path,
    )
    pd.DataFrame({"app_id": sorted(already_done)}).to_csv(fetched_path, index=False)
    with open(done_marker, "w") as f:
        f.write("done\n")

    log(f"  {len(df):,} apps with app-only domain and/or market ID blocks")
    return deserialize_list_columns(df, ["app_only_domains", "app_only_market_ids"])


# ── Step 3: Demand-side spend (vx_overview) ───────────────────────────────────

def collect_blocked_entities(account_blocks_df, app_blocks_df):
    """Unique blocked domains / market IDs from step 2 — only these need demand spend."""
    domains = set()
    markets = set()

    if account_blocks_df is not None and not account_blocks_df.empty:
        for _, row in account_blocks_df.iterrows():
            for d in row.get("blocked_domains") or []:
                key = normalize_domain(d)
                if key:
                    domains.add(key)
            for m in row.get("blocked_market_ids") or []:
                key = normalize_market_id(m)
                if key:
                    markets.add(key)

    if app_blocks_df is not None and not app_blocks_df.empty:
        for _, row in app_blocks_df.iterrows():
            for d in row.get("app_only_domains") or []:
                key = normalize_domain(d)
                if key:
                    domains.add(key)
            for m in row.get("app_only_market_ids") or []:
                key = normalize_market_id(m)
                if key:
                    markets.add(key)

    return sorted(domains), sorted(markets)


def fetch_high_spend_global(token, fields, sorts, label):
    """
    Fetch top high-spend rows from vx_overview (no entity-ID filter).
    Matches internal tool pattern: spend filter + limit + join in build step.
    """
    date_filter = f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
    filters = {
        "vx_overview.event_date": date_filter,
        "vx_overview.unified_ad_spend": f">={TOTAL_THRESHOLD}",
    }
    log(
        f"  {label}: global top spenders (≥${SPEND_THRESHOLD:,}/day, "
        f"limit {ADV_SPEND_LOOKER_LIMIT:,})..."
    )
    return run_query(
        token,
        "vx_analytics",
        "vx_overview",
        fields,
        filters,
        sorts=sorts,
        limit=ADV_SPEND_LOOKER_LIMIT,
    )


def fetch_network_total(token):
    cached = load_cache(CACHE_FILES["network_total"])
    if cached is not None:
        total_daily = float(cached["total_daily_spend"].iloc[0])
        log(f"  Total network daily spend: ${total_daily:,.2f} (from cache)")
        return total_daily

    raw = run_query(
        token,
        "vx_analytics",
        "vx_overview",
        ["vx_overview.unified_ad_spend"],
        {"vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"},
        limit=1,
    )
    total_7d = pd.to_numeric(raw[0]["vx_overview.unified_ad_spend"], errors="coerce")
    total_daily = float(total_7d / LOOKBACK_DAYS)
    save_cache(
        pd.DataFrame([{"total_spend_7d": round(total_7d, 2), "total_daily_spend": round(total_daily, 4)}]),
        CACHE_FILES["network_total"],
    )
    log(f"  Total network daily spend: ${total_daily:,.2f}")
    return total_daily


def fetch_domain_spend(token):
    cached = load_cache(CACHE_FILES["domain_spend"])
    if cached is not None:
        out = cached.copy()
        out["daily_spend"] = pd.to_numeric(out["daily_spend"], errors="coerce").fillna(0)
        log(f"  {len(out):,} high-spend domains (from cache)")
        return out

    raw = fetch_high_spend_global(
        token,
        [LOOKER_FIELD_ADOMAIN, LOOKER_FIELD_SPEND],
        sorts=[f"{LOOKER_FIELD_SPEND} desc"],
        label="domains",
    )

    if not raw:
        df = pd.DataFrame(columns=["lookup_key", "adomain", "daily_spend"])
        save_cache(df, CACHE_FILES["domain_spend"])
        log("  No domain spend rows returned", "WARN")
        return df

    df = looker_rows_to_df(
        raw,
        {LOOKER_FIELD_ADOMAIN: "adomain", LOOKER_FIELD_SPEND: "total_spend_7d"},
    )
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["adomain"] = df["adomain"].fillna("").astype(str).str.strip()
    df = df[df["adomain"] != ""].copy()
    df["lookup_key"] = df["adomain"].map(normalize_domain)

    df = (
        df.groupby("lookup_key", as_index=False)
        .agg(adomain=("adomain", "first"), total_spend_7d=("total_spend_7d", "sum"))
    )
    df["daily_spend"] = (df["total_spend_7d"] / LOOKBACK_DAYS).round(4)
    df = df[df["daily_spend"] >= SPEND_THRESHOLD].copy()
    df = df.sort_values("daily_spend", ascending=False).reset_index(drop=True)
    save_cache(df[["lookup_key", "adomain", "daily_spend"]], CACHE_FILES["domain_spend"])
    log(f"  {len(df):,} domains ≥ ${SPEND_THRESHOLD:,}/day")
    return df


def fetch_market_spend(token):
    cached = load_cache(CACHE_FILES["market_spend"])
    if cached is not None:
        out = cached.copy()
        out["daily_spend"] = pd.to_numeric(out["daily_spend"], errors="coerce").fillna(0)
        log(f"  {len(out):,} high-spend market IDs (from cache)")
        return out

    raw = fetch_high_spend_global(
        token,
        [LOOKER_FIELD_ADV_BUNDLE, LOOKER_FIELD_ADV_TITLE, LOOKER_FIELD_SPEND],
        sorts=[f"{LOOKER_FIELD_SPEND} desc"],
        label="market_ids",
    )

    if not raw:
        df = pd.DataFrame(columns=["lookup_key", "adv_bundle", "adv_title", "daily_spend"])
        save_cache(df, CACHE_FILES["market_spend"])
        log("  No market ID spend rows returned", "WARN")
        return df

    df = looker_rows_to_df(
        raw,
        {
            LOOKER_FIELD_ADV_BUNDLE: "adv_bundle",
            LOOKER_FIELD_ADV_TITLE: "adv_title",
            LOOKER_FIELD_SPEND: "total_spend_7d",
        },
    )
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["adv_bundle"] = df["adv_bundle"].fillna("").astype(str).str.strip()
    df["adv_title"] = df["adv_title"].fillna("").astype(str).str.strip()
    df = df[df["adv_bundle"].notna() & (df["adv_bundle"] != "") & (df["adv_bundle"] != "nan")].copy()
    df["lookup_key"] = df["adv_bundle"].map(normalize_market_id)

    df = (
        df.groupby("lookup_key", as_index=False)
        .agg(
            adv_bundle=("adv_bundle", "first"),
            adv_title=("adv_title", "first"),
            total_spend_7d=("total_spend_7d", "sum"),
        )
    )
    df["daily_spend"] = (df["total_spend_7d"] / LOOKBACK_DAYS).round(4)
    df = df[df["daily_spend"] >= SPEND_THRESHOLD].copy()
    df = df.sort_values("daily_spend", ascending=False).reset_index(drop=True)
    save_cache(df[["lookup_key", "adv_bundle", "adv_title", "daily_spend"]], CACHE_FILES["market_spend"])
    log(f"  {len(df):,} market IDs ≥ ${SPEND_THRESHOLD:,}/day")
    return df


def fetch_demand_spend(token, account_blocks_df, app_blocks_df):
    log("STEP 3: Fetch demand-side spend (vx_overview)", "STEP")
    blocked_domains, blocked_markets = collect_blocked_entities(account_blocks_df, app_blocks_df)
    log(f"  Blocked entities to match: {len(blocked_domains):,} domains, {len(blocked_markets):,} market IDs")
    log("  Using global high-spend fetch + in-memory join (same pattern as internal tool)", "STEP")

    total_daily = fetch_network_total(token)
    domain_df = fetch_domain_spend(token)
    market_df = fetch_market_spend(token)

    if blocked_domains and not domain_df.empty:
        domain_keys = set(domain_df["lookup_key"].astype(str))
        matched = sum(1 for d in blocked_domains if d in domain_keys)
        log(f"  Blocked domains matched in high-spend lookup: {matched:,} / {len(blocked_domains):,}")
    if blocked_markets and not market_df.empty:
        market_keys = set(market_df["lookup_key"].astype(str))
        matched = sum(1 for m in blocked_markets if m in market_keys)
        log(f"  Blocked market IDs matched in high-spend lookup: {matched:,} / {len(blocked_markets):,}")

    return total_daily, domain_df, market_df


# ── Step 4: Build audit ───────────────────────────────────────────────────────

def build_audit(
    apps_df,
    accounts_df,
    account_blocks_df,
    app_blocks_df,
    total_network_daily,
    domain_df,
    market_df,
):
    log("STEP 4: Build combined audit", "STEP")

    domain_lookup = safe_lookup(domain_df, "lookup_key", ["adomain", "daily_spend"])
    market_lookup = safe_lookup(market_df, "lookup_key", ["adv_bundle", "adv_title", "daily_spend"])

    account_meta = safe_lookup(
        accounts_df,
        "account_id",
        ["account_name", "region", "am_name", "daily_spend"],
    )
    app_meta = safe_lookup(
        apps_df,
        "app_id",
        ["account_id", "account_name", "app_name", "pub_market_id", "region", "am_name", "daily_spend"],
    )

    qualifying_account_ids = set(accounts_df["account_id"].astype(str).str.strip())
    rows = []

    def append_row(
        *,
        account_id,
        level,
        block_type,
        block_value,
        app_id="",
        app_name="",
        pub_market_id="",
        pub_daily_spend=0,
        adv_title="",
        adv_daily_spend=0,
    ):
        meta = account_meta.get(account_id, {})
        account_name = meta.get("account_name", "")
        region = meta.get("region", "")
        am_name = meta.get("am_name", "")

        if level == "account":
            pub_daily_spend = float(meta.get("daily_spend", pub_daily_spend) or 0)
        else:
            app_info = app_meta.get(app_id, {})
            account_name = app_info.get("account_name", account_name)
            region = app_info.get("region", region)
            am_name = app_info.get("am_name", am_name)
            pub_daily_spend = float(app_info.get("daily_spend", pub_daily_spend) or 0)

        network_share = adv_daily_spend / total_network_daily if total_network_daily > 0 else 0
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
                "block_type": block_type,
                "block_value": block_value,
                "adv_title": adv_title,
                "network_share": round(network_share, 8),
                "adv_daily_spend": round(adv_daily_spend, 4),
                "est_uplift_daily": est_uplift,
            }
        )

    log("  Processing account-level blocks...")
    acct_before = len(rows)
    if not account_blocks_df.empty:
        for _, row in account_blocks_df.iterrows():
            account_id = str(row["account_id"]).strip()
            if account_id not in qualifying_account_ids:
                continue

            for domain_key in row.get("blocked_domains") or []:
                domain_key = normalize_domain(domain_key)
                info = domain_lookup.get(domain_key)
                if not info:
                    continue
                append_row(
                    account_id=account_id,
                    level="account",
                    block_type="domain",
                    block_value=info.get("adomain", domain_key),
                    adv_daily_spend=float(info.get("daily_spend", 0)),
                )

            for market_key in row.get("blocked_market_ids") or []:
                market_key = normalize_market_id(market_key)
                info = market_lookup.get(market_key)
                if not info:
                    continue
                append_row(
                    account_id=account_id,
                    level="account",
                    block_type="market id",
                    block_value=info.get("adv_bundle", market_key),
                    adv_title=info.get("adv_title", ""),
                    adv_daily_spend=float(info.get("daily_spend", 0)),
                )
    log(f"    Account-level opportunities: {len(rows) - acct_before:,}")

    log("  Processing app-level blocks (app-only lists from API)...")
    app_before = len(rows)
    if not app_blocks_df.empty:
        for _, row in app_blocks_df.iterrows():
            app_id = str(row["app_id"]).strip()
            account_id = str(row["account_id"]).strip()
            if app_id not in app_meta:
                continue

            app_info = app_meta[app_id]

            for domain_key in row.get("app_only_domains") or []:
                domain_key = normalize_domain(domain_key)
                info = domain_lookup.get(domain_key)
                if not info:
                    continue
                append_row(
                    account_id=account_id,
                    level="app",
                    block_type="domain",
                    block_value=info.get("adomain", domain_key),
                    app_id=app_id,
                    app_name=app_info.get("app_name", ""),
                    pub_market_id=app_info.get("pub_market_id", ""),
                    adv_daily_spend=float(info.get("daily_spend", 0)),
                )

            for market_key in row.get("app_only_market_ids") or []:
                market_key = normalize_market_id(market_key)
                info = market_lookup.get(market_key)
                if not info:
                    continue
                append_row(
                    account_id=account_id,
                    level="app",
                    block_type="market id",
                    block_value=info.get("adv_bundle", market_key),
                    app_id=app_id,
                    app_name=app_info.get("app_name", ""),
                    pub_market_id=app_info.get("pub_market_id", ""),
                    adv_title=info.get("adv_title", ""),
                    adv_daily_spend=float(info.get("daily_spend", 0)),
                )
    log(f"    App-level opportunities: {len(rows) - app_before:,}")

    output = pd.DataFrame(rows)
    if not output.empty:
        output = output.sort_values("est_uplift_daily", ascending=False).reset_index(drop=True)

    save_cache(output, CACHE_FILES["final"])
    output.to_csv("output/audit_pub_blocked_domains_combined.csv", index=False)
    log(f"Combined audit → {CACHE_FILES['final']} ({len(output):,} rows)")
    return output


def main():
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    if "--refresh" in sys.argv:
        clear_caches()

    token = get_looker_token()
    apps_df, accounts_df = fetch_supply(token)
    account_blocks_df = fetch_account_blocklists(accounts_df)
    app_blocks_df = fetch_app_blocklists(apps_df, account_blocks_df)
    total_network_daily, domain_df, market_df = fetch_demand_spend(token, account_blocks_df, app_blocks_df)
    output = build_audit(
        apps_df,
        accounts_df,
        account_blocks_df,
        app_blocks_df,
        total_network_daily,
        domain_df,
        market_df,
    )

    log("═" * 60, "STEP")
    log("AUDIT COMPLETE", "STEP")
    if not output.empty:
        log(f"Total opportunities: {len(output):,}")
        log(f"  Account-level: {(output['level'] == 'account').sum():,}")
        log(f"  App-level:     {(output['level'] == 'app').sum():,}")
        log(f"  Domain blocks: {(output['block_type'] == 'domain').sum():,}")
        log(f"  Market ID:     {(output['block_type'] == 'market id').sum():,}")
        print("\nTop 10 by est_uplift_daily:")
        print(
            output[
                [
                    "pub_account",
                    "pub_app",
                    "level",
                    "block_type",
                    "block_value",
                    "adv_daily_spend",
                    "est_uplift_daily",
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
