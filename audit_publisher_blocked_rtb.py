"""
Publisher Blocking RTB Audit (Dashboard API)
Fetches publishers blocking high-spend RTB accounts and connections.

Uses:
- Looker for spend data
- Publisher Dashboard GraphQL API for blocklists

Outputs:
  - output/audit_publisher_blocked_rtb_accounts.csv
  - output/audit_publisher_blocked_rtb_connections.csv
"""

import os
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

os.makedirs("output", exist_ok=True)


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


def run_looker_query(token, model, view, fields, filters, limit=50000):
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json={
            "model": model,
            "view": view,
            "fields": fields,
            "filters": filters,
            "limit": str(limit)
        },
        timeout=300
    )
    resp.raise_for_status()
    return resp.json()


# ── Dashboard GraphQL API ─────────────────────────────────────────────────────

def fetch_account_blocklists_from_api(account_id):
    """Fetch rtbAccountBlocklist and rtbConnectionBlocklist for a single account."""
    query = """
    query account($id: String!) {
      account(id: $id) {
        id
        name
        rtbAccountBlocklist
        rtbConnectionBlocklist
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
            "rtb_account_blocklist": account.get("rtbAccountBlocklist", []),
            "rtb_connection_blocklist": account.get("rtbConnectionBlocklist", [])
        }
    
    except Exception as e:
        print(f"  ⚠ Error fetching account {account_id}: {e}")
        return None


# ── Step 1: Fetch publisher account spend ─────────────────────────────────────

def fetch_publisher_spend(token):
    print("\n[1/3] Fetching publisher account spend...")
    data = run_looker_query(
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
    
    df = pd.DataFrame(data)
    df.columns = ["publisher_account_id", "publisher_account_name", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["publisher_daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df.groupby(["publisher_account_id", "publisher_account_name"], dropna=False).agg({
        "publisher_daily_spend": "sum"
    }).reset_index()
    
    # Filter to high-spend publishers
    df = df[df["publisher_daily_spend"] >= SPEND_THRESHOLD].copy()
    df = df[df["publisher_account_id"].notna() & (df["publisher_account_id"] != "")].copy()
    df["publisher_account_id"] = df["publisher_account_id"].astype(str).str.strip()
    df = df.drop_duplicates(subset=["publisher_account_id"], keep="first")
    
    print(f"  ✓ {len(df)} high-spend publishers (>${SPEND_THRESHOLD}/day)")
    return df


# ── Step 2: Fetch RTB account & connection spend ──────────────────────────────

def fetch_rtb_account_spend(token):
    print("\n[2/3] Fetching RTB account spend...")
    data = run_looker_query(
        token,
        model="vx_analytics",
        view="vx_overview",
        fields=[
            "rtb_accounts.id",
            "rtb_accounts.name",
            "vx_overview.unified_ad_spend"
        ],
        filters={
            "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
        }
    )
    
    df = pd.DataFrame(data)
    df.columns = ["rtb_account_id", "rtb_account_name", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["rtb_account_daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df.groupby(["rtb_account_id", "rtb_account_name"], dropna=False).agg({
        "rtb_account_daily_spend": "sum"
    }).reset_index()
    
    # Filter to high-spend RTB accounts
    df = df[df["rtb_account_daily_spend"] >= SPEND_THRESHOLD].copy()
    df = df[df["rtb_account_id"].notna() & (df["rtb_account_id"] != "")].copy()
    df["rtb_account_id"] = df["rtb_account_id"].astype(str).str.strip()
    
    total_spend = df["rtb_account_daily_spend"].sum()
    
    print(f"  ✓ {len(df)} high-spend RTB accounts (>${SPEND_THRESHOLD}/day)")
    print(f"  ✓ Total RTB account daily spend: ${total_spend:,.2f}")
    return df, total_spend


def fetch_rtb_connection_spend(token):
    print("\n[3/3] Fetching RTB connection spend...")
    data = run_looker_query(
        token,
        model="vx_analytics",
        view="vx_overview",
        fields=[
            "rtb_connections.id",
            "rtb_connections.name",
            "vx_overview.unified_ad_spend"
        ],
        filters={
            "vx_overview.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
        }
    )
    
    df = pd.DataFrame(data)
    df.columns = ["rtb_connection_id", "rtb_connection_name", "total_spend_7d"]
    df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
    df["rtb_connection_daily_spend"] = df["total_spend_7d"] / LOOKBACK_DAYS
    df = df.groupby(["rtb_connection_id", "rtb_connection_name"], dropna=False).agg({
        "rtb_connection_daily_spend": "sum"
    }).reset_index()
    
    # Filter to high-spend RTB connections
    df = df[df["rtb_connection_daily_spend"] >= SPEND_THRESHOLD].copy()
    df = df[df["rtb_connection_id"].notna() & (df["rtb_connection_id"] != "")].copy()
    df["rtb_connection_id"] = df["rtb_connection_id"].astype(str).str.strip()
    
    total_spend = df["rtb_connection_daily_spend"].sum()
    
    print(f"  ✓ {len(df)} high-spend RTB connections (>${SPEND_THRESHOLD}/day)")
    print(f"  ✓ Total RTB connection daily spend: ${total_spend:,.2f}")
    return df, total_spend


# ── Step 3: Fetch blocklists via Dashboard API ────────────────────────────────

def fetch_all_blocklists(pub_spend_df):
    """Fetch blocklists for all high-spend publisher accounts via Dashboard API."""
    print("\n[4/4] Fetching blocklists from Dashboard API...")
    print(f"  (Processing {len(pub_spend_df)} accounts...)")
    
    blocklists = []
    total = len(pub_spend_df)
    
    for i, (_, row) in enumerate(pub_spend_df.iterrows(), 1):
        account_id = row["publisher_account_id"]
        
        if i % 50 == 0 or i == total:
            print(f"    Progress: {i}/{total} accounts...")
        
        result = fetch_account_blocklists_from_api(account_id)
        
        if result:
            blocklists.append(result)
        
        # Rate limiting
        time.sleep(0.05)
    
    df = pd.DataFrame(blocklists)
    
    # Filter to accounts with blocks
    df = df[
        (df["rtb_account_blocklist"].apply(lambda x: len(x) > 0 if isinstance(x, list) else False)) |
        (df["rtb_connection_blocklist"].apply(lambda x: len(x) > 0 if isinstance(x, list) else False))
    ].copy()
    
    print(f"  ✓ {len(df)} accounts with RTB blocks")
    return df


def safe_lookup(df, key_col, value_cols):
    """Build a unique-key dict lookup. Collapses duplicate keys so to_dict never fails."""
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
        if "spend" in c.lower() or "daily" in c.lower():
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


# ── Build blocked RTB accounts audit ─────────────────────────────────────────

def build_blocked_accounts_audit(pub_spend_df, blocklists_df, rtb_spend_df, total_rtb_spend):
    print("\nBuilding blocked RTB accounts audit...")
    
    rtb_lookup = safe_lookup(
        rtb_spend_df, "rtb_account_id", ["rtb_account_name", "rtb_account_daily_spend"]
    )
    pub_spend_lookup = safe_lookup(
        pub_spend_df, "publisher_account_id", ["publisher_account_name", "publisher_daily_spend"]
    )
    
    rows = []
    
    for _, row in blocklists_df.iterrows():
        account_id = str(row["account_id"]).strip()
        rtb_account_blocklist = row["rtb_account_blocklist"]
        
        if not isinstance(rtb_account_blocklist, list) or len(rtb_account_blocklist) == 0:
            continue
        
        pub_info = pub_spend_lookup.get(account_id, {})
        pub_name = pub_info.get("publisher_account_name", "")
        pub_daily_spend = pub_info.get("publisher_daily_spend", 0)
        
        for blocked_id in rtb_account_blocklist:
            blocked_id = str(blocked_id).strip()
            rtb_info = rtb_lookup.get(blocked_id)
            
            if not rtb_info:
                continue  # Not a high-spend RTB account
            
            rtb_name = rtb_info.get("rtb_account_name", "")
            rtb_daily_spend = rtb_info.get("rtb_account_daily_spend", 0)
            
            # Uplift = (blocked RTB spend / total RTB network spend) × publisher daily spend
            pct_of_network = rtb_daily_spend / total_rtb_spend if total_rtb_spend > 0 else 0
            uplift = pct_of_network * pub_daily_spend
            
            rows.append({
                "publisher_account_id": account_id,
                "publisher_account_name": pub_name,
                "publisher_daily_spend": round(pub_daily_spend, 2),
                "blocked_rtb_account_id": blocked_id,
                "blocked_rtb_account_name": rtb_name,
                "rtb_account_daily_spend": round(rtb_daily_spend, 2),
                "rtb_pct_of_network": round(pct_of_network * 100, 2),
                "potential_uplift": round(uplift, 2)
            })
    
    output = pd.DataFrame(rows)
    if not output.empty:
        output = output.sort_values("potential_uplift", ascending=False).reset_index(drop=True)
    
    output.to_csv("output/audit_publisher_blocked_rtb_accounts.csv", index=False)
    print(f"✓ {len(output)} opportunities → output/audit_publisher_blocked_rtb_accounts.csv")
    return output


# ── Build blocked RTB connections audit ───────────────────────────────────────

def build_blocked_connections_audit(pub_spend_df, blocklists_df, rtb_conn_df, total_rtb_conn_spend):
    print("Building blocked RTB connections audit...")
    
    rtb_conn_lookup = safe_lookup(
        rtb_conn_df, "rtb_connection_id", ["rtb_connection_name", "rtb_connection_daily_spend"]
    )
    pub_spend_lookup = safe_lookup(
        pub_spend_df, "publisher_account_id", ["publisher_account_name", "publisher_daily_spend"]
    )
    
    rows = []
    
    for _, row in blocklists_df.iterrows():
        account_id = str(row["account_id"]).strip()
        rtb_connection_blocklist = row["rtb_connection_blocklist"]
        
        if not isinstance(rtb_connection_blocklist, list) or len(rtb_connection_blocklist) == 0:
            continue
        
        pub_info = pub_spend_lookup.get(account_id, {})
        pub_name = pub_info.get("publisher_account_name", "")
        pub_daily_spend = pub_info.get("publisher_daily_spend", 0)
        
        for blocked_id in rtb_connection_blocklist:
            blocked_id = str(blocked_id).strip()
            rtb_info = rtb_conn_lookup.get(blocked_id)
            
            if not rtb_info:
                continue  # Not a high-spend RTB connection
            
            rtb_name = rtb_info.get("rtb_connection_name", "")
            rtb_daily_spend = rtb_info.get("rtb_connection_daily_spend", 0)
            
            # Uplift = (blocked RTB connection spend / total RTB connection network spend) × publisher daily spend
            pct_of_network = rtb_daily_spend / total_rtb_conn_spend if total_rtb_conn_spend > 0 else 0
            uplift = pct_of_network * pub_daily_spend
            
            rows.append({
                "publisher_account_id": account_id,
                "publisher_account_name": pub_name,
                "publisher_daily_spend": round(pub_daily_spend, 2),
                "blocked_rtb_connection_id": blocked_id,
                "blocked_rtb_connection_name": rtb_name,
                "rtb_connection_daily_spend": round(rtb_daily_spend, 2),
                "rtb_pct_of_network": round(pct_of_network * 100, 2),
                "potential_uplift": round(uplift, 2)
            })
    
    output = pd.DataFrame(rows)
    if not output.empty:
        output = output.sort_values("potential_uplift", ascending=False).reset_index(drop=True)
    
    output.to_csv("output/audit_publisher_blocked_rtb_connections.csv", index=False)
    print(f"✓ {len(output)} opportunities → output/audit_publisher_blocked_rtb_connections.csv")
    return output


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")
    
    token = get_looker_token()
    
    # Fetch spend data from Looker
    pub_spend_df = fetch_publisher_spend(token)
    rtb_account_df, total_rtb_account_spend = fetch_rtb_account_spend(token)
    rtb_conn_df, total_rtb_conn_spend = fetch_rtb_connection_spend(token)
    
    # Fetch blocklists from Dashboard API
    blocklists_df = fetch_all_blocklists(pub_spend_df)
    
    # Build audits
    accounts_output = build_blocked_accounts_audit(
        pub_spend_df, blocklists_df, rtb_account_df, total_rtb_account_spend
    )
    connections_output = build_blocked_connections_audit(
        pub_spend_df, blocklists_df, rtb_conn_df, total_rtb_conn_spend
    )
    
    print(f"\n{'='*60}")
    print(f"AUDIT COMPLETE")
    print(f"  Blocked RTB account opportunities: {len(accounts_output)}")
    print(f"  Blocked RTB connection opportunities: {len(connections_output)}")
    
    if not accounts_output.empty:
        print(f"\nTop 5 blocked RTB account opportunities:")
        print(accounts_output[[
            "publisher_account_name", "blocked_rtb_account_name",
            "publisher_daily_spend", "rtb_account_daily_spend", "potential_uplift"
        ]].head().to_string(index=False))
    
    if not connections_output.empty:
        print(f"\nTop 5 blocked RTB connection opportunities:")
        print(connections_output[[
            "publisher_account_name", "blocked_rtb_connection_name",
            "publisher_daily_spend", "rtb_connection_daily_spend", "potential_uplift"
        ]].head().to_string(index=False))
    
    print(f"{'='*60}")
