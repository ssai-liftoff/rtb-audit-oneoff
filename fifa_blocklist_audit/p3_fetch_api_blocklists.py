"""
FIFA Blocklist Audit — Part 3: Fetch Publisher Blocklists via API

For every qualifying publisher account and app from Part 1, calls the
pub-gateway-api to retrieve:

  Account level:
    - adDomainBlacklist   (blocked adomains)
    - adCatBlocklist      (blocked category internal codes, e.g. V1-6)

  App level (same + exemptions):
    - adDomainBlacklist
    - adCatBlocklist
    - blocklistExemptions (per-domain/category whitelist carve-outs)

Uses both GraphQL queries with threaded execution (MAX_WORKERS parallel calls).

Inputs:  output/fifa_blocklist_audit/p1_supply_accounts.csv
         output/fifa_blocklist_audit/p1_supply_apps.csv
Outputs: output/fifa_blocklist_audit/p3_account_blocklists.csv
         output/fifa_blocklist_audit/p3_app_blocklists.csv
"""

import os
import json
import requests
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

OUTPUT_DIR  = "output/fifa_blocklist_audit"
MAX_WORKERS = 25

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Auth tokens (hardcoded — replace when expired) ──────────────────────────
# Both tokens hit the same endpoint; separate tokens in case one expires first.
APP_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2NvdW50IjoiNjgxM2JmZDUxMDRiNDAwMDExMzcyNDM2Iiw"
    "iaXNNYW5hZ2VkQWNjb3VudCI6ZmFsc2UsImFwaVRva2VuIjoiN2FhMThlNTdmMTU0NmQ2YmQ1NDJlMDc2YWI5NzI3"
    "YzMiLCJlbWFpbCI6InNzYWlAbGlmdG9mZi5pbyIsImZlYXR1cmVzIjpbImZlYXR1cmVfbWVkaWF0aW9uX2FjY2Vzc"
    "yJdLCJpZCI6IjY4MTNiZmQ1MTA0YjQwMDAxMTM3MjQzYSIsImlzX2RlbGV0ZWQiOmZhbHNlLCJpc0ludGVybmFsIjp"
    "0cnVlLCJyb2xlIjoiYWNjb3VudF9zdHJhdGVneSIsInNvdXJjZSI6IiIsInVpZCI6IjAwMDAwMDAwMDAwMDAwMDAwMD"
    "AwMDAwMCIsImV4cCI6MTc4MTA5OTc5Mn0.6qd4OruhLadpyrBJiz3PSuBQSs3GjSZumRJAV9C3hHI"
)

ACCOUNT_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2NvdW50IjoiNjgxM2JmZDUxMDRiNDAwMDExMzcyNDM2Iiw"
    "iaXNNYW5hZ2VkQWNjb3VudCI6ZmFsc2UsImFwaVRva2VuIjoiN2FhMThlNTdmMTU0NmQ2YmQ1NDJlMDc2YWI5NzI3"
    "YzMiLCJlbWFpbCI6InNzYWlAbGlmdG9mZi5pbyIsImZlYXR1cmVzIjpbImZlYXR1cmVfbWVkaWF0aW9uX2FjY2Vzc"
    "yJdLCJpZCI6IjY4MTNiZmQ1MTA0YjQwMDAxMTM3MjQzYSIsImlzX2RlbGV0ZWQiOmZhbHNlLCJpc0ludGVybmFsIjp"
    "0cnVlLCJyb2xlIjoiYWNjb3VudF9zdHJhdGVneSIsInNvdXJjZSI6IiIsInVpZCI6IjAwMDAwMDAwMDAwMDAwMDAwMD"
    "AwMDAwMCIsImV4cCI6MTc4MTEwMDkyMn0.XpaBBOV2P3VcDQib24dLpglO3HQ9kJfhzBIWRhr0HFM"
)

API_URL = "https://pub-gateway-api.vungle.com/query"

COMMON_HEADERS = {
    "Accept-Language":  "en-GB,en-US;q=0.9,en;q=0.8",
    "Connection":       "keep-alive",
    "Origin":           "https://pubadmin.vungle.com",
    "Referer":          "https://pubadmin.vungle.com/",
    "accept":           "application/json, text/plain, */*",
    "content-type":     "application/json",
    "vungle-source":    "admin",
    "vungle-version":   "1",
    "User-Agent":       "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

# Minimal GraphQL queries — only fetch the fields we actually need
ACCOUNT_QUERY = """
query account($id: String!) {
  account(id: $id) {
    id
    name
    adDomainBlacklist
    adCatBlocklist
  }
}
"""

APP_QUERY = """
query application($id: String!) {
  application(id: $id) {
    id
    name
    adDomainBlacklist
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


def fetch_account(account_id):
    try:
        for attempt in range(3):
            resp = requests.post(
                API_URL,
                headers={**COMMON_HEADERS, "authorization": f"Bearer {ACCOUNT_TOKEN}"},
                json={
                    "operationName": "account",
                    "variables":     {"page": 0, "perPage": 0, "id": account_id},
                    "query":         ACCOUNT_QUERY,
                },
                timeout=20,
            )
            if resp.status_code == 429:
                import time; time.sleep(2 ** attempt)
                continue
            break
        if resp.status_code == 401:
            raise ValueError("ACCOUNT_TOKEN expired — update ACCOUNT_TOKEN in p3_fetch_api_blocklists.py")
        resp.raise_for_status()
        data = (resp.json().get("data") or {}).get("account") or {}

        return {
            "account_id":        account_id,
            "domain_blocklist":  json.dumps(data.get("adDomainBlacklist") or []),
            "cat_blocklist":     json.dumps(data.get("adCatBlocklist") or []),
            "error":             None,
        }
    except ValueError:
        raise
    except Exception as e:
        return {
            "account_id":       account_id,
            "domain_blocklist": "[]",
            "cat_blocklist":    "[]",
            "error":            str(e),
        }


def fetch_app(app_id):
    try:
        for attempt in range(3):
            resp = requests.post(
                API_URL,
                headers={**COMMON_HEADERS, "authorization": f"Bearer {APP_TOKEN}"},
                json={
                    "operationName": "application",
                    "variables":     {"reportIncluded": False, "id": app_id},
                    "query":         APP_QUERY,
                },
                timeout=20,
            )
            if resp.status_code == 429:
                import time; time.sleep(2 ** attempt)
                continue
            break
        if resp.status_code == 401:
            raise ValueError("APP_TOKEN expired — update APP_TOKEN in p3_fetch_api_blocklists.py")
        resp.raise_for_status()
        data = (resp.json().get("data") or {}).get("application") or {}

        return {
            "app_id":           app_id,
            "domain_blocklist": json.dumps(data.get("adDomainBlacklist") or []),
            "cat_blocklist":    json.dumps(data.get("adCatBlocklist") or []),
            "exemptions":       json.dumps(data.get("blocklistExemptions") or []),
            "error":            None,
        }
    except ValueError:
        raise
    except Exception as e:
        return {
            "app_id":           app_id,
            "domain_blocklist": "[]",
            "cat_blocklist":    "[]",
            "exemptions":       "[]",
            "error":            str(e),
        }


def run_parallel(ids, fetch_fn, label):
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_fn, eid): eid for eid in ids}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            if i % 50 == 0 or i == len(ids):
                log(f"  {label}: {i}/{len(ids)}")
    return results


if __name__ == "__main__":
    acct_cache = f"{OUTPUT_DIR}/p3_account_blocklists.csv"
    app_cache  = f"{OUTPUT_DIR}/p3_app_blocklists.csv"

    # ── Accounts ────────────────────────────────────────────────────────────
    if os.path.exists(acct_cache):
        log(f"Cache found — loading {acct_cache}")
        acct_df = pd.read_csv(acct_cache)
        log(f"  {len(acct_df):,} account blocklists loaded")
    else:
        log("═" * 60, "STEP")
        log("FIFA BLOCKLIST AUDIT — PART 3: FETCH API BLOCKLISTS", "STEP")
        log("═" * 60, "STEP")

        p1_acct = f"{OUTPUT_DIR}/p1_supply_accounts.csv"
        if not os.path.exists(p1_acct):
            raise FileNotFoundError(f"Not found: {p1_acct} — run p1_fetch_supply.py first")

        accounts = pd.read_csv(p1_acct)["account_id"].dropna().astype(str).unique().tolist()
        log(f"Fetching blocklists for {len(accounts):,} accounts...")

        results = run_parallel(accounts, fetch_account, "accounts")
        acct_df = pd.DataFrame(results)

        errors = acct_df["error"].notna().sum()
        if errors:
            log(f"  {errors} account fetch errors", "WARN")

        acct_df.to_csv(acct_cache, index=False)
        log(f"Saved → {acct_cache}")

    # ── Apps ─────────────────────────────────────────────────────────────────
    if os.path.exists(app_cache):
        log(f"Cache found — loading {app_cache}")
        app_df = pd.read_csv(app_cache)
        log(f"  {len(app_df):,} app blocklists loaded")
    else:
        p1_apps = f"{OUTPUT_DIR}/p1_supply_apps.csv"
        if not os.path.exists(p1_apps):
            raise FileNotFoundError(f"Not found: {p1_apps} — run p1_fetch_supply.py first")

        apps = pd.read_csv(p1_apps)["app_id"].dropna().astype(str).unique().tolist()
        log(f"Fetching blocklists for {len(apps):,} apps...")

        results = run_parallel(apps, fetch_app, "apps")
        app_df = pd.DataFrame(results)

        errors = app_df["error"].notna().sum()
        if errors:
            log(f"  {errors} app fetch errors", "WARN")

        app_df.to_csv(app_cache, index=False)
        log(f"Saved → {app_cache}")

    # ── Summary ──────────────────────────────────────────────────────────────
    def count_non_empty(df, col):
        return (df[col].fillna("[]").apply(lambda x: len(json.loads(x)) > 0)).sum()

    log("═" * 60, "STEP")
    log(f"Accounts fetched:              {len(acct_df):,}")
    log(f"  With domain blocks:          {count_non_empty(acct_df, 'domain_blocklist'):,}")
    log(f"  With category blocks:        {count_non_empty(acct_df, 'cat_blocklist'):,}")
    log(f"Apps fetched:                  {len(app_df):,}")
    log(f"  With domain blocks:          {count_non_empty(app_df, 'domain_blocklist'):,}")
    log(f"  With category blocks:        {count_non_empty(app_df, 'cat_blocklist'):,}")
    log(f"  With exemptions:             {count_non_empty(app_df, 'exemptions'):,}")
    log("Next: run p4_build_audit.py", "STEP")
    log("═" * 60, "STEP")
