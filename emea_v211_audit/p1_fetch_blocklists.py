"""
EMEA V2-11 Society Block Audit — Part 1: Fetch API Blocklists

Reads input_publishers.csv (pre-filtered EMEA publisher accounts + apps with spend).
For each unique account and app, calls pub-gateway-api to retrieve:
  - adCatBlocklist  (blocked internal category codes)
  - blocklistExemptions (app-level only — per-category whitelist carve-outs)

Runs threaded for speed.

Input:  emea_v211_audit/input_publishers.csv
Output: output/emea_v211_audit/p1_account_blocklists.csv
        output/emea_v211_audit/p1_app_blocklists.csv
"""

import os
import json
import requests
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

INPUT_CSV  = "emea_v211_audit/input_publishers.csv"
OUTPUT_DIR = "output/emea_v211_audit"
MAX_WORKERS = 25

os.makedirs(OUTPUT_DIR, exist_ok=True)

ACCOUNT_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2NvdW50IjoiNjgxM2JmZDUxMDRiNDAwMDExMzcyNDM2Iiw"
    "iaXNNYW5hZ2VkQWNjb3VudCI6ZmFsc2UsImFwaVRva2VuIjoiN2FhMThlNTdmMTU0NmQ2YmQ1NDJlMDc2YWI5NzI3"
    "YzMiLCJlbWFpbCI6InNzYWlAbGlmdG9mZi5pbyIsImZlYXR1cmVzIjpbImZlYXR1cmVfbWVkaWF0aW9uX2FjY2Vzc"
    "yJdLCJpZCI6IjY4MTNiZmQ1MTA0YjQwMDAxMTM3MjQzYSIsImlzX2RlbGV0ZWQiOmZhbHNlLCJpc0ludGVybmFsIjp"
    "0cnVlLCJyb2xlIjoiYWNjb3VudF9zdHJhdGVneSIsInNvdXJjZSI6IiIsInVpZCI6IjAwMDAwMDAwMDAwMDAwMDAwMD"
    "AwMDAwMCIsImV4cCI6MTc4MTYwOTczMn0.gYErgjc0Gw1OznWTZ1pOFiMtFusWFuCt8VEgflI36PA"
)

APP_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2NvdW50IjoiNjgxM2JmZDUxMDRiNDAwMDExMzcyNDM2Iiw"
    "iaXNNYW5hZ2VkQWNjb3VudCI6ZmFsc2UsImFwaVRva2VuIjoiN2FhMThlNTdmMTU0NmQ2YmQ1NDJlMDc2YWI5NzI3"
    "YzMiLCJlbWFpbCI6InNzYWlAbGlmdG9mZi5pbyIsImZlYXR1cmVzIjpbImZlYXR1cmVfbWVkaWF0aW9uX2FjY2Vzc"
    "yJdLCJpZCI6IjY4MTNiZmQ1MTA0YjQwMDAxMTM3MjQzYSIsImlzX2RlbGV0ZWQiOmZhbHNlLCJpc0ludGVybmFsIjp"
    "0cnVlLCJyb2xlIjoiYWNjb3VudF9zdHJhdGVneSIsInNvdXJjZSI6IiIsInVpZCI6IjAwMDAwMDAwMDAwMDAwMDAwMD"
    "AwMDAwMCIsImV4cCI6MTc4MTYwOTc4MX0.4RSL71MS2k6RPbPTprs017smjkUmmCl2SLago5sbvGY"
)

API_URL = "https://pub-gateway-api.vungle.com/query"

HEADERS = {
    "accept":           "application/json, text/plain, */*",
    "content-type":     "application/json",
    "vungle-source":    "admin",
    "vungle-version":   "1",
    "Origin":           "https://pubadmin.vungle.com",
    "Referer":          "https://pubadmin.vungle.com/",
    "User-Agent":       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

ACCOUNT_QUERY = """
query account($id: String!) {
  account(id: $id) {
    id
    name
    adCatBlocklist
  }
}
"""

APP_QUERY = """
query application($id: String!) {
  application(id: $id) {
    id
    name
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
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def api_post(token, body):
    for attempt in range(3):
        resp = requests.post(
            API_URL,
            headers={**HEADERS, "authorization": f"Bearer {token}"},
            json=body,
            timeout=20,
        )
        if resp.status_code == 429:
            import time; time.sleep(2 ** attempt)
            continue
        break
    if resp.status_code == 401:
        raise ValueError(f"Token expired — update token in p1_fetch_blocklists.py")
    resp.raise_for_status()
    return (resp.json().get("data") or {})


def fetch_account(account_id):
    try:
        data = api_post(ACCOUNT_TOKEN, {
            "operationName": "account",
            "variables":     {"page": 0, "perPage": 0, "id": account_id},
            "query":         ACCOUNT_QUERY,
        })
        acc = data.get("account") or {}
        return {
            "account_id":    account_id,
            "cat_blocklist": json.dumps(acc.get("adCatBlocklist") or []),
            "error":         None,
        }
    except ValueError:
        raise
    except Exception as e:
        return {"account_id": account_id, "cat_blocklist": "[]", "error": str(e)}


def fetch_app(app_id):
    try:
        data = api_post(APP_TOKEN, {
            "operationName": "application",
            "variables":     {"reportIncluded": False, "id": app_id},
            "query":         APP_QUERY,
        })
        app = data.get("application") or {}
        return {
            "app_id":        app_id,
            "cat_blocklist": json.dumps(app.get("adCatBlocklist") or []),
            "exemptions":    json.dumps(app.get("blocklistExemptions") or []),
            "error":         None,
        }
    except ValueError:
        raise
    except Exception as e:
        return {"app_id": app_id, "cat_blocklist": "[]", "exemptions": "[]", "error": str(e)}


def run_parallel(ids, fetch_fn, label):
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_fn, eid): eid for eid in ids}
        for i, future in enumerate(as_completed(futures), 1):
            results.append(future.result())
            if i % 100 == 0 or i == len(ids):
                log(f"  {label}: {i}/{len(ids)}")
    return results


if __name__ == "__main__":
    log("═" * 60, "STEP")
    log("EMEA V2-11 AUDIT — PART 1: FETCH API BLOCKLISTS", "STEP")
    log("═" * 60, "STEP")

    df = pd.read_csv(INPUT_CSV)
    df.columns = ["account_id", "account_name", "app_id", "app_name", "spend"]
    df["account_id"] = df["account_id"].astype(str).str.strip()
    df["app_id"]     = df["app_id"].astype(str).str.strip()
    log(f"Input: {len(df):,} rows, {df['account_id'].nunique():,} unique accounts, {df['app_id'].nunique():,} unique apps")

    # ── Accounts ─────────────────────────────────────────────────────────
    acct_cache = f"{OUTPUT_DIR}/p1_account_blocklists.csv"
    if os.path.exists(acct_cache):
        log(f"Cache found — loading {acct_cache}")
        acct_df = pd.read_csv(acct_cache)
    else:
        account_ids = df["account_id"].unique().tolist()
        log(f"Fetching blocklists for {len(account_ids):,} accounts...")
        acct_df = pd.DataFrame(run_parallel(account_ids, fetch_account, "accounts"))
        errors = acct_df["error"].notna().sum()
        if errors:
            log(f"  {errors} fetch errors (likely deleted/inaccessible accounts)", "WARN")
        acct_df.to_csv(acct_cache, index=False)
        log(f"Saved → {acct_cache}")

    # ── Apps ──────────────────────────────────────────────────────────────
    app_cache = f"{OUTPUT_DIR}/p1_app_blocklists.csv"
    if os.path.exists(app_cache):
        log(f"Cache found — loading {app_cache}")
        app_df = pd.read_csv(app_cache)
    else:
        app_ids = df["app_id"].unique().tolist()
        log(f"Fetching blocklists for {len(app_ids):,} apps...")
        app_df = pd.DataFrame(run_parallel(app_ids, fetch_app, "apps"))
        errors = app_df["error"].notna().sum()
        if errors:
            log(f"  {errors} fetch errors (likely deleted/inaccessible apps)", "WARN")
        app_df.to_csv(app_cache, index=False)
        log(f"Saved → {app_cache}")

    # ── Summary ───────────────────────────────────────────────────────────
    def has_v211(bl_str):
        try:
            return "V2-11" in json.loads(bl_str or "[]")
        except Exception:
            return False

    acct_v211 = acct_df["cat_blocklist"].apply(has_v211).sum()
    app_v211  = app_df["cat_blocklist"].apply(has_v211).sum()

    log("═" * 60, "STEP")
    log(f"Accounts with V2-11 blocked: {acct_v211:,} / {len(acct_df):,}")
    log(f"Apps with V2-11 blocked:     {app_v211:,} / {len(app_df):,}")
    log("Next: run p2_build_audit.py", "STEP")
    log("═" * 60, "STEP")
