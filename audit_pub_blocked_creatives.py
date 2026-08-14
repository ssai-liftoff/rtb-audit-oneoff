"""
Publisher Creative ID Blocks Audit (Dashboard API)

Fetches publisher account/app blocked creatives via Dashboard API,
splits RTB ID from external ID, and calculates uplift based on
RTB account × creative combo spend.

Uplift = (combo daily spend / total combo network spend) × pub daily spend
  - Account-level blocks → multiply by account daily spend
  - App-level blocks     → multiply by app daily spend

Output columns:
  account_id, account_name, app_id, app_name, region, am_name, level,
  pub_daily_spend, blocked_creative_id, rtb_id, external_id, adomain,
  creative_daily_spend, potential_uplift_daily

Output:
  - output/audit_pub_blocked_creatives_combined.csv
"""

import ast
import json
import os
import sys
import time
import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

# Hardcoded dashboard API token
DASHBOARD_API_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2NvdW50IjoiNjgxM2JmZDUxMDRiNDAwMDExMzcyNDM2IiwiaXNNYW5hZ2VkQWNjb3VudCI6ZmFsc2UsImFwaVRva2VuIjoiN2FhMThlNTdmMTU0NmQ2YmQ1NDJlMDc2YWI5NzI3YzMiLCJlbWFpbCI6InNzYWlAbGlmdG9mZi5pbyIsImZlYXR1cmVzIjpbImZlYXR1cmVfbWVkaWF0aW9uX2FjY2VzcyJdLCJpZCI6IjY4MTNiZmQ1MTA0YjQwMDAxMTM3MjQzYSIsImlzX2RlbGV0ZWQiOmZhbHNlLCJpc0ludGVybmFsIjp0cnVlLCJyb2xlIjoiYWNjb3VudF9zdHJhdGVneSIsInNvdXJjZSI6IiIsInVpZCI6IjAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMCIsImV4cCI6MTc4NTg1OTc0N30.OQUNrOQFk4mrtIVIttoK8rZwjw8zphFXDkU7qt9adTo"

DASHBOARD_API_URL = "https://pub-gateway-api.vungle.com/query"

LOOKBACK_DAYS = 7
SPEND_THRESHOLD = 500

OUTPUT_DIR = "output/pub_blocked_creatives"
os.makedirs(OUTPUT_DIR, exist_ok=True)

CACHE_FILES = {
    "creatives": f"{OUTPUT_DIR}/p1_creative_spend.csv",
    "accounts": f"{OUTPUT_DIR}/p2_account_spend.csv",
    "apps": f"{OUTPUT_DIR}/p2_app_spend.csv",
    "account_blocks": f"{OUTPUT_DIR}/p3_account_blocklists.csv",
    "app_blocks": f"{OUTPUT_DIR}/p4_app_blocklists.csv",
    "final": f"{OUTPUT_DIR}/audit_pub_blocked_creatives_combined.csv",
}


def load_cache(path):
    if os.path.exists(path):
        print(f"  ✓ Cache hit — loading {path}")
        return pd.read_csv(path)
    return None


def save_cache(df, path):
    df.to_csv(path, index=False)
    print(f"  ✓ Saved cache → {path} ({len(df):,} rows)")


def parse_blocklist_cell(val):
    """Deserialize creative_blocklist stored as JSON/list string in CSV."""
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
    try:
        parsed = ast.literal_eval(str(val))
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return []


def serialize_blocklists(df):
    """Convert list column to JSON strings for CSV storage."""
    out = df.copy()
    if "creative_blocklist" in out.columns:
        out["creative_blocklist"] = out["creative_blocklist"].apply(
            lambda x: json.dumps(x) if isinstance(x, list) else (x if pd.notna(x) else "[]")
        )
    return out


def deserialize_blocklists(df):
    """Convert creative_blocklist JSON strings back to lists."""
    out = df.copy()
    if "creative_blocklist" in out.columns:
        out["creative_blocklist"] = out["creative_blocklist"].apply(parse_blocklist_cell)
    return out


def clear_caches():
    print("Clearing all cached files...")
    extra = [
        f"{OUTPUT_DIR}/p4_apps_fetched.csv",
        f"{CACHE_FILES['app_blocks']}.done",
    ]
    for path in list(CACHE_FILES.values()) + extra:
        if os.path.exists(path):
            os.remove(path)
            print(f"  deleted {path}")
    print("Cache cleared.")


def safe_lookup(df, key_col, value_cols):
    """Build a unique-key dict lookup. Collapses duplicate keys so to_dict never fails.
    Spend-like columns are summed; everything else keeps first non-null value.
    """
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
        if "spend" in c.lower() or "daily" in c.lower() or "revenue" in c.lower():
            work[c] = pd.to_numeric(work[c], errors="coerce").fillna(0)
            agg[c] = "sum"
        else:
            agg[c] = "first"

    before = len(work)
    work = work.groupby(key_col, as_index=False).agg(agg)
    dupes = before - len(work)
    if dupes > 0:
        print(f"  ⚠ Collapsed {dupes} duplicate '{key_col}' rows before lookup")

    return work.set_index(key_col)[list(agg.keys())].to_dict("index")


# ── Looker Auth ───────────────────────────────────────────────────────────────

def get_looker_token():
    print("Authenticating with Looker API...")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}"
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    print("✓ Authenticated")
    return token


def looker_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def run_looker_query(token, model, view, fields, filters, sorts=None, limit=50000):
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
        json=payload,
        timeout=300
    )
    
    if not resp.ok:
        print(f"  ✗ Looker error {resp.status_code}: {resp.text[:500]}")
    
    resp.raise_for_status()
    return resp.json()


# ── Dashboard GraphQL API ─────────────────────────────────────────────────────

def fetch_account_creative_blocklist_from_api(account_id):
    """Fetch eDSPCreativeIdBlocklist for a publisher account."""
    query = """
    query account($id: String!) {
      account(id: $id) {
        id
        eDSPCreativeIdBlocklist
        __typename
      }
    }
    """
    
    try:
        resp = requests.post(
            DASHBOARD_API_URL,
            headers={
                "authorization": f"Bearer {DASHBOARD_API_TOKEN}",
                "content-type": "application/json",
                "vungle-source": "admin",
                "vungle-version": "1"
            },
            json={
                "operationName": "account",
                "variables": {"id": account_id},
                "query": query
            },
            timeout=10
        )
        
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        account = data.get("data", {}).get("account")
        
        if not account:
            return None
        
        return {
            "account_id": account.get("id"),
            "creative_blocklist": account.get("eDSPCreativeIdBlocklist", [])
        }
    
    except Exception as e:
        print(f"  ⚠ Error fetching account {account_id}: {e}")
        return None


def fetch_app_creative_blocklist_from_api(app_id):
    """Fetch eDSPCreativeIdBlocklist for a publisher app."""
    query = """
    query application($id: String!) {
      application(id: $id) {
        id
        owner
        eDSPCreativeIdBlocklist
        __typename
      }
    }
    """
    
    try:
        resp = requests.post(
            DASHBOARD_API_URL,
            headers={
                "authorization": f"Bearer {DASHBOARD_API_TOKEN}",
                "content-type": "application/json",
                "vungle-source": "admin",
                "vungle-version": "1"
            },
            json={
                "operationName": "application",
                "variables": {"id": app_id},
                "query": query
            },
            timeout=10
        )
        
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        app = data.get("data", {}).get("application")
        
        if not app:
            return None
        
        return {
            "app_id": app.get("id"),
            "account_id": app.get("owner"),
            "creative_blocklist": app.get("eDSPCreativeIdBlocklist", [])
        }
    
    except Exception as e:
        print(f"  ⚠ Error fetching app {app_id}: {e}")
        return None


# ── Step 1: Fetch RTB account × creative combo spend ──────────────────────────

def fetch_creative_combo_spend(token):
    """Fetch network-wide spend per RTB account × creative ID combination."""
    print("\n[1/4] Fetching RTB account × creative combo spend...")
    print("  (This may take a few minutes...)")

    cached = load_cache(CACHE_FILES["creatives"])
    if cached is not None and "rtb_account_id" in cached.columns:
        cached["rtb_account_id"] = cached["rtb_account_id"].astype(str).str.strip()
        cached["creative_id"] = cached["creative_id"].astype(str).str.strip()
        cached["daily_spend"] = pd.to_numeric(cached["daily_spend"], errors="coerce").fillna(0)
        cached = (
            cached.groupby(["rtb_account_id", "creative_id"], as_index=False)
            .agg({"adomain": "first", "total_spend_7d": "sum", "daily_spend": "sum"})
        )
        print(f"  ✓ {len(cached)} high-spend RTB × creative combos (from cache)")
        return cached

    if cached is not None:
        print("  ⚠ Stale cache (missing rtb_account_id) — re-fetching...")

    data = run_looker_query(
        token,
        model="vx_analytics",
        view="vx_overview",
        fields=[
            "rtb_accounts.id",
            "vx_overview.creative_id",
            "vx_overview.adomain",
            "vx_overview.unified_ad_spend"
        ],
        filters={"vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"},
        sorts=["vx_overview.unified_ad_spend desc"],
        limit=50000
    )
    
    df = pd.DataFrame(data)
    df.columns = ["rtb_account_id", "creative_id", "adomain", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    
    df = df[
        df["rtb_account_id"].notna() & (df["rtb_account_id"] != "") &
        df["creative_id"].notna() & (df["creative_id"] != "")
    ].copy()
    df["rtb_account_id"] = df["rtb_account_id"].astype(str).str.strip()
    df["creative_id"] = df["creative_id"].astype(str).str.strip()

    # Filter to high-spend combos, then collapse duplicates
    df = df[df["daily_spend"] >= SPEND_THRESHOLD].copy()
    before = len(df)
    df = (
        df.groupby(["rtb_account_id", "creative_id"], as_index=False)
        .agg({"adomain": "first", "total_spend_7d": "sum", "daily_spend": "sum"})
    )
    if before != len(df):
        print(f"  ⚠ Collapsed {before - len(df)} duplicate RTB × creative rows")
    
    print(f"  ✓ {len(df)} high-spend RTB × creative combos (>${SPEND_THRESHOLD}/day)")
    save_cache(df, CACHE_FILES["creatives"])
    return df


# ── Step 2: Fetch publisher account metadata + app spend together ────────────

def fetch_pub_account_and_app_spend(token):
    """Fetch both account and app spend in one step."""
    print("\n[2/4] Fetching publisher account + app spend...")

    cached_accounts = load_cache(CACHE_FILES["accounts"])
    cached_apps = load_cache(CACHE_FILES["apps"])
    apps_cache_ok = (
        cached_apps is not None
        and "app_name" in cached_apps.columns
        and (cached_apps["app_name"].fillna("").astype(str).str.strip() != "").any()
    )

    if cached_accounts is not None:
        account_df = cached_accounts.copy()
        account_df["account_id"] = account_df["account_id"].astype(str).str.strip()
        account_df["daily_spend"] = pd.to_numeric(account_df["daily_spend"], errors="coerce").fillna(0)
        for col in ["region", "am_name"]:
            if col not in account_df.columns:
                account_df[col] = ""
            else:
                account_df[col] = account_df[col].fillna("")
        print(f"  ✓ {len(account_df)} accounts (from cache)")
    else:
        account_data = run_looker_query(
            token,
            model="vx_analytics",
            view="vx_overview",
            fields=[
                "publisher_accounts.id",
                "publisher_accounts.name",
                "vx_overview.unified_ad_spend"
            ],
            filters={
                "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
            }
        )

        account_df = pd.DataFrame(account_data)
        account_df.columns = ["account_id", "account_name", "total_spend_7d"]
        account_df["total_spend_7d"] = pd.to_numeric(account_df["total_spend_7d"], errors="coerce").fillna(0)
        account_df["daily_spend"] = account_df["total_spend_7d"] / LOOKBACK_DAYS
        account_df = account_df.groupby(["account_id", "account_name"], dropna=False).agg({
            "daily_spend": "sum"
        }).reset_index()
        account_df = account_df[account_df["daily_spend"] >= SPEND_THRESHOLD].copy()
        account_df = account_df[account_df["account_id"].notna() & (account_df["account_id"] != "")].copy()
        account_df["account_id"] = account_df["account_id"].astype(str).str.strip()
        account_df = account_df.drop_duplicates(subset=["account_id"], keep="first")

        try:
            print("    Fetching region and AM metadata...")
            account_ids = list(account_df["account_id"])
            metadata = run_looker_query(
                token,
                model="dmx_reports",
                view="publisher_report",
                fields=[
                    "publisher_accounts.id",
                    "salesforce_accounts_monetize.am_user_region",
                    "salesforce_accounts_monetize.am_user_name"
                ],
                filters={
                    "publisher_accounts.id": ",".join(account_ids),
                    "publisher_report.event_date": "1 days"
                }
            )
            meta_df = pd.DataFrame(metadata)
            if len(meta_df.columns) == 3:
                meta_df.columns = ["account_id", "region", "am_name"]
            elif len(meta_df.columns) == 2:
                meta_df.columns = ["account_id", "region"]
                meta_df["am_name"] = ""
            else:
                meta_df = pd.DataFrame({"account_id": account_ids, "region": "", "am_name": ""})
            meta_df["account_id"] = meta_df["account_id"].astype(str).str.strip()
            meta_df = meta_df.drop_duplicates(subset=["account_id"])
            account_df = account_df.merge(meta_df[["account_id", "region", "am_name"]], on="account_id", how="left")
            account_df["region"] = account_df["region"].fillna("")
            account_df["am_name"] = account_df["am_name"].fillna("")
        except Exception as e:
            print(f"    ⚠ Could not fetch region/AM metadata: {e}")
            account_df["region"] = ""
            account_df["am_name"] = ""

        save_cache(account_df, CACHE_FILES["accounts"])
        print(f"  ✓ {len(account_df)} high-spend accounts (>${SPEND_THRESHOLD}/day)")

    if apps_cache_ok:
        app_df = cached_apps.copy()
        app_df["app_id"] = app_df["app_id"].astype(str).str.strip()
        app_df["account_id"] = app_df["account_id"].astype(str).str.strip()
        app_df["daily_spend"] = pd.to_numeric(app_df["daily_spend"], errors="coerce").fillna(0)
        app_df["app_name"] = app_df["app_name"].fillna("").astype(str).str.strip()
        print(f"  ✓ {len(app_df)} apps (from cache)")
        return account_df, app_df

    if cached_apps is not None:
        print("  ⚠ App name missing in cache — re-fetching app spend only...")

    app_data = run_looker_query(
        token,
        model="vx_analytics",
        view="vx_overview",
        fields=[
            "publisher_apps.id",
            "publisher_apps.name",
            "publisher_accounts.id",
            "vx_overview.unified_ad_spend"
        ],
        filters={
            "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
        }
    )

    app_df = pd.DataFrame(app_data)
    app_df.columns = ["app_id", "app_name", "account_id", "total_spend_7d"]
    app_df["total_spend_7d"] = pd.to_numeric(app_df["total_spend_7d"], errors="coerce").fillna(0)
    app_df["daily_spend"] = app_df["total_spend_7d"] / LOOKBACK_DAYS
    app_df = app_df[app_df["daily_spend"] >= SPEND_THRESHOLD].copy()
    app_df = app_df[
        app_df["app_id"].notna() & (app_df["app_id"] != "") &
        app_df["account_id"].notna() & (app_df["account_id"] != "")
    ].copy()
    app_df["app_id"] = app_df["app_id"].astype(str).str.strip()
    app_df["account_id"] = app_df["account_id"].astype(str).str.strip()
    app_df["app_name"] = app_df["app_name"].fillna("").astype(str).str.strip()
    app_df = (
        app_df.groupby(["app_id", "account_id"], as_index=False)
        .agg({"total_spend_7d": "sum", "daily_spend": "sum", "app_name": "first"})
    )

    save_cache(app_df, CACHE_FILES["apps"])
    print(f"  ✓ {len(app_df)} high-spend apps (>${SPEND_THRESHOLD}/day)")
    return account_df, app_df


# ── Step 3: Fetch account-level creative blocklists via API ───────────────────

def fetch_all_account_blocklists(account_metadata_df):
    """Fetch account-level creative blocklists from Dashboard API."""
    print("\n[3/4] Fetching account-level creative blocklists from Dashboard API...")

    cached = load_cache(CACHE_FILES["account_blocks"])
    if cached is not None:
        df = deserialize_blocklists(cached)
        if not df.empty and "account_id" in df.columns:
            df["account_id"] = df["account_id"].astype(str).str.strip()
        print(f"  ✓ {len(df)} accounts with creative blocks (from cache)")
        return df

    print(f"  (Processing {len(account_metadata_df)} accounts...)")
    
    blocklists = []
    total = len(account_metadata_df)
    
    for i, (_, row) in enumerate(account_metadata_df.iterrows(), 1):
        account_id = row["account_id"]
        
        if i % 50 == 0 or i == total:
            print(f"    Progress: {i}/{total} accounts...")
        
        result = fetch_account_creative_blocklist_from_api(account_id)
        
        if result and result["creative_blocklist"]:
            blocklists.append(result)
        
        time.sleep(0.05)  # Rate limiting
    
    df = pd.DataFrame(blocklists)
    if not df.empty:
        df["account_id"] = df["account_id"].astype(str).str.strip()
        df = df.drop_duplicates(subset=["account_id"], keep="first")
    print(f"  ✓ {len(df)} accounts with creative blocks")
    save_cache(serialize_blocklists(df), CACHE_FILES["account_blocks"])
    return df


# ── Step 4: Fetch app-level creative blocklists via API ───────────────────────

def fetch_all_app_blocklists(app_spend_df):
    """Fetch app-level creative blocklists from Dashboard API.
    Supports resume via a fetched-IDs checkpoint (includes empty blocklists).
    """
    print("\n[4/4] Fetching app-level creative blocklists from Dashboard API...")

    cache_path = CACHE_FILES["app_blocks"]
    fetched_path = f"{OUTPUT_DIR}/p4_apps_fetched.csv"
    done_marker = f"{cache_path}.done"

    # Fully completed cache
    if os.path.exists(done_marker) and os.path.exists(cache_path):
        cached = load_cache(cache_path)
        if cached is not None:
            df = deserialize_blocklists(cached)
            print(f"  ✓ {len(df)} apps with creative blocks (from cache)")
            return df

    # Resume from partial progress
    blocklists = []
    already_done = set()

    if os.path.exists(cache_path):
        partial = deserialize_blocklists(pd.read_csv(cache_path))
        if not partial.empty and "app_id" in partial.columns:
            blocklists = partial.to_dict("records")

    if os.path.exists(fetched_path):
        fetched_df = pd.read_csv(fetched_path)
        if "app_id" in fetched_df.columns:
            already_done = set(fetched_df["app_id"].astype(str).str.strip())
            print(f"  ✓ Resuming — {len(already_done)} apps already fetched")

    remaining = app_spend_df[~app_spend_df["app_id"].astype(str).isin(already_done)]
    total = len(app_spend_df)
    print(f"  (Processing {len(remaining)} remaining of {total} apps...)")

    for i, (_, row) in enumerate(remaining.iterrows(), 1):
        app_id = str(row["app_id"]).strip()
        done_count = len(already_done) + i

        if i % 100 == 0 or i == len(remaining):
            print(f"    Progress: {done_count}/{total} apps...")

        result = fetch_app_creative_blocklist_from_api(app_id)
        already_done.add(app_id)

        if result and result.get("creative_blocklist"):
            blocklists.append(result)

        # Checkpoint every 100 apps
        if i % 100 == 0:
            save_cache(serialize_blocklists(pd.DataFrame(blocklists)), cache_path)
            pd.DataFrame({"app_id": sorted(already_done)}).to_csv(fetched_path, index=False)

        time.sleep(0.05)  # Rate limiting

    df = pd.DataFrame(blocklists)
    print(f"  ✓ {len(df)} apps with creative blocks")
    save_cache(serialize_blocklists(df), cache_path)
    pd.DataFrame({"app_id": sorted(already_done)}).to_csv(fetched_path, index=False)
    with open(done_marker, "w") as f:
        f.write("done\n")
    return df


# ── Build combined audit with deduplication ────────────────────────────────────

def split_creative_id(blocked_id):
    """Split 'RTBaccountID_creativeID' by first underscore."""
    parts = str(blocked_id).split("_", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    # If no underscore, treat entire string as external ID
    return "", parts[0].strip()


def build_combined_audit(
    account_metadata_df,
    account_blocklist_df,
    app_spend_df,
    app_blocklist_df,
    combo_df
):
    """Build combined account + app level audit with deduplication."""
    print("\nBuilding combined audit...")
    
    # RTB × creative combo lookup: rtb_id||creative_id → {adomain, daily_spend}
    combo_df = combo_df.copy()
    combo_df["lookup_key"] = combo_df["rtb_account_id"] + "||" + combo_df["creative_id"]
    combo_lookup = safe_lookup(combo_df, "lookup_key", ["adomain", "daily_spend"])
    total_combo_spend = combo_df["daily_spend"].sum()
    print(f"  Total RTB × creative network daily spend: ${total_combo_spend:,.2f}")

    account_meta_lookup = safe_lookup(
        account_metadata_df, "account_id", ["account_name", "region", "am_name", "daily_spend"]
    )
    
    # Build account-level blocked creative set per account (for dedup)
    account_blocks_by_account = {}
    if not account_blocklist_df.empty:
        for _, row in account_blocklist_df.iterrows():
            account_id = str(row["account_id"]).strip()
            creative_list = row["creative_blocklist"]
            if isinstance(creative_list, list):
                account_blocks_by_account[account_id] = set(str(c).strip() for c in creative_list)
    
    rows = []
    
    # ── Process account-level blocks ──
    print("  Processing account-level blocks...")
    if not account_blocklist_df.empty:
        for _, row in account_blocklist_df.iterrows():
            account_id = str(row["account_id"]).strip()
            creative_blocklist = row["creative_blocklist"]
            
            if not isinstance(creative_blocklist, list):
                continue
            
            meta = account_meta_lookup.get(account_id, {})
            account_name = meta.get("account_name", "")
            region = meta.get("region", "")
            am_name = meta.get("am_name", "")
            account_daily_spend = meta.get("daily_spend", 0)
            
            for blocked_id in creative_blocklist:
                blocked_id = str(blocked_id).strip()
                rtb_id, external_id = split_creative_id(blocked_id)
                
                lookup_key = f"{rtb_id}||{external_id}"
                combo_info = combo_lookup.get(lookup_key)
                
                if not combo_info:
                    continue  # No matching high-spend RTB × creative combo
                
                creative_daily_spend = combo_info.get("daily_spend", 0)
                adomain = combo_info.get("adomain", "")
                
                pct_of_network = (
                    creative_daily_spend / total_combo_spend if total_combo_spend > 0 else 0
                )
                potential_uplift = round(pct_of_network * account_daily_spend, 2)
                
                rows.append({
                    "account_id": account_id,
                    "account_name": account_name,
                    "app_id": "",
                    "app_name": "",
                    "region": region,
                    "am_name": am_name,
                    "level": "account",
                    "pub_daily_spend": round(account_daily_spend, 2),
                    "blocked_creative_id": blocked_id,
                    "rtb_id": rtb_id,
                    "external_id": external_id,
                    "adomain": adomain,
                    "creative_daily_spend": round(creative_daily_spend, 2),
                    "potential_uplift_daily": potential_uplift
                })
    
    account_count = len(rows)
    print(f"    Account-level opportunities: {account_count}")
    
    # ── Process app-level blocks (with deduplication) ──
    print("  Processing app-level blocks (deduping against account-level)...")
    if not app_blocklist_df.empty:
        for _, row in app_blocklist_df.iterrows():
            app_id = str(row["app_id"]).strip()
            account_id = str(row["account_id"]).strip()
            creative_blocklist = row["creative_blocklist"]
            
            if not isinstance(creative_blocklist, list):
                continue
            
            # Get app info
            app_info = app_spend_df[app_spend_df["app_id"].astype(str).str.strip() == app_id]
            if app_info.empty:
                continue
            
            app_info = app_info.iloc[0]
            app_name = app_info["app_name"]
            app_daily_spend = app_info["daily_spend"]
            
            # Get account metadata
            meta = account_meta_lookup.get(account_id, {})
            account_name = meta.get("account_name", "")
            region = meta.get("region", "")
            am_name = meta.get("am_name", "")
            
            # Get account-level blocks for this account (for deduplication)
            account_blocks = account_blocks_by_account.get(account_id, set())
            
            for blocked_id in creative_blocklist:
                blocked_id = str(blocked_id).strip()
                
                # DEDUP: Skip if already blocked at account level
                if blocked_id in account_blocks:
                    continue
                
                rtb_id, external_id = split_creative_id(blocked_id)
                
                lookup_key = f"{rtb_id}||{external_id}"
                combo_info = combo_lookup.get(lookup_key)
                
                if not combo_info:
                    continue
                
                creative_daily_spend = combo_info.get("daily_spend", 0)
                adomain = combo_info.get("adomain", "")
                
                pct_of_network = (
                    creative_daily_spend / total_combo_spend if total_combo_spend > 0 else 0
                )
                potential_uplift = round(pct_of_network * app_daily_spend, 2)
                
                rows.append({
                    "account_id": account_id,
                    "account_name": account_name,
                    "app_id": app_id,
                    "app_name": app_name,
                    "region": region,
                    "am_name": am_name,
                    "level": "app",
                    "pub_daily_spend": round(app_daily_spend, 2),
                    "blocked_creative_id": blocked_id,
                    "rtb_id": rtb_id,
                    "external_id": external_id,
                    "adomain": adomain,
                    "creative_daily_spend": round(creative_daily_spend, 2),
                    "potential_uplift_daily": potential_uplift
                })
    
    app_count = len(rows) - account_count
    print(f"    App-level opportunities (after dedup): {app_count}")
    
    # Build final dataframe
    output = pd.DataFrame(rows)
    
    if not output.empty:
        output = output.sort_values("potential_uplift_daily", ascending=False).reset_index(drop=True)
    
    output_path = CACHE_FILES["final"]
    # Also keep a copy at the old location for convenience
    output.to_csv(output_path, index=False)
    output.to_csv("output/audit_pub_blocked_creatives_combined.csv", index=False)
    
    print(f"\n✓ Combined audit saved → {output_path}")
    print(f"  Total opportunities: {len(output)}")
    if not output.empty:
        print(f"    Account-level: {(output['level'] == 'account').sum()}")
        print(f"    App-level: {(output['level'] == 'app').sum()}")
    
    return output


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    if "--refresh" in sys.argv:
        clear_caches()
    
    token = get_looker_token()
    
    # Fetch all data (each step caches to disk)
    combo_df = fetch_creative_combo_spend(token)
    account_metadata_df, app_spend_df = fetch_pub_account_and_app_spend(token)
    
    # Fetch blocklists from Dashboard API
    account_blocklist_df = fetch_all_account_blocklists(account_metadata_df)
    app_blocklist_df = fetch_all_app_blocklists(app_spend_df)
    
    # Build combined audit with deduplication
    output = build_combined_audit(
        account_metadata_df,
        account_blocklist_df,
        app_spend_df,
        app_blocklist_df,
        combo_df
    )
    
    print(f"\n{'='*60}")
    print(f"AUDIT COMPLETE")
    print(f"{'='*60}")
    
    if not output.empty:
        print(f"\nTop 10 opportunities by potential uplift:")
        print(output[[
            "account_name", "app_name", "level", "blocked_creative_id",
            "adomain", "creative_daily_spend", "potential_uplift_daily"
        ]].head(10).to_string(index=False))
    
    print(f"\n{'='*60}")
    print("Cached intermediates under output/pub_blocked_creatives/")
    print("Re-run skips completed steps. Force refresh with: --refresh")
    print(f"{'='*60}")
