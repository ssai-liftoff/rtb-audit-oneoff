"""
Whitelist Audit — Part 2: Fetch Publisher App Whitelists via API

For every qualifying publisher app from Part 1, calls the pub-gateway-api to
retrieve blocklistExemptions — the per-domain/bundle whitelist carve-outs that
allow specific advertiser domains or app bundles through an otherwise blocked
category.

Note: blocklistExemptions only exist at the app level (not account level).
      rtbAccountId and country fields are intentionally excluded.

Uses threaded execution (MAX_WORKERS parallel calls).

Input:  output/whitelist_audit/p1_supply_apps.csv
Output: output/whitelist_audit/p2_app_whitelists.csv
"""

import os
import json
import time
import requests
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

OUTPUT_DIR  = "output/whitelist_audit"
MAX_WORKERS = 25

os.makedirs(OUTPUT_DIR, exist_ok=True)

API_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2NvdW50IjoiNjgxM2JmZDUxMDRiNDAwMDEx"
    "MzcyNDM2IiwiaXNNYW5hZ2VkQWNjb3VudCI6ZmFsc2UsImFwaVRva2VuIjoiN2FhMThlNTdmMTU0Nm"
    "Q2YmQ1NDJlMDc2YWI5NzI3YzMiLCJlbWFpbCI6InNzYWlAbGlmdG9mZi5pbyIsImZlYXR1cmVzIjpb"
    "ImZlYXR1cmVfbWVkaWF0aW9uX2FjY2VzcyJdLCJpZCI6IjY4MTNiZmQ1MTA0YjQwMDAxMTM3MjQzYS"
    "IsImlzX2RlbGV0ZWQiOmZhbHNlLCJpc0ludGVybmFsIjp0cnVlLCJyb2xlIjoiYWNjb3VudF9zdHJh"
    "dGVneSIsInNvdXJjZSI6IiIsInVpZCI6IjAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMCIsImV4cCI6MTc4"
    "MzAwNjg1NH0.ircwVG7L0GPLLZfYUgSF68GqFySuru__P-glud_kQ5U"
)

API_URL = "https://pub-gateway-api.vungle.com/query"

HEADERS = {
    "Accept-Language": "en-GB,en-US;q=0.9,en;q=0.8",
    "Connection":      "keep-alive",
    "Origin":          "https://pubadmin.vungle.com",
    "Referer":         "https://pubadmin.vungle.com/",
    "accept":          "application/json, text/plain, */*",
    "content-type":    "application/json",
    "vungle-source":   "admin",
    "vungle-version":  "1",
    "User-Agent":      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

# Only fetch what we need — rtbAccountId and country intentionally omitted
APP_QUERY = """
query application($id: String!) {
  application(id: $id) {
    id
    name
    blocklistExemptions {
      advCatId
      domain
      bundle
    }
  }
}
"""


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def fetch_app(app_id):
    try:
        for attempt in range(3):
            resp = requests.post(
                API_URL,
                headers={**HEADERS, "authorization": f"Bearer {API_TOKEN}"},
                json={
                    "operationName": "application",
                    "variables":     {"reportIncluded": False, "id": app_id},
                    "query":         APP_QUERY,
                },
                timeout=20,
            )
            if resp.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            break

        if resp.status_code == 401:
            raise ValueError("API_TOKEN expired — update API_TOKEN in p2_fetch_whitelists.py")
        resp.raise_for_status()

        data = (resp.json().get("data") or {}).get("application") or {}
        exemptions = data.get("blocklistExemptions") or []

        return {
            "app_id":      app_id,
            "exemptions":  json.dumps(exemptions),
            "exempt_count": len(exemptions),
            "error":       None,
        }
    except ValueError:
        raise
    except Exception as e:
        return {
            "app_id":      app_id,
            "exemptions":  "[]",
            "exempt_count": 0,
            "error":       str(e),
        }


if __name__ == "__main__":
    cache = f"{OUTPUT_DIR}/p2_app_whitelists.csv"

    if os.path.exists(cache):
        log(f"Cache found — loading {cache}")
        df = pd.read_csv(cache)
        log(f"  {len(df):,} app whitelist records loaded")
        has_exemptions = (df["exempt_count"].fillna(0) > 0).sum()
        log(f"  {has_exemptions:,} apps have at least one exemption")
    else:
        log("═" * 60, "STEP")
        log("WHITELIST AUDIT — PART 2: FETCH APP WHITELISTS", "STEP")
        log("═" * 60, "STEP")

        p1_path = f"{OUTPUT_DIR}/p1_supply_apps.csv"
        if not os.path.exists(p1_path):
            raise FileNotFoundError(f"Not found: {p1_path} — run p1_fetch_supply.py first")

        app_ids = (
            pd.read_csv(p1_path)["app_id"]
            .dropna()
            .astype(str)
            .str.strip()
            .unique()
            .tolist()
        )
        log(f"Fetching whitelists for {len(app_ids):,} unique apps...")

        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(fetch_app, aid): aid for aid in app_ids}
            for i, future in enumerate(as_completed(futures), 1):
                result = future.result()
                results.append(result)
                if i % 100 == 0 or i == len(app_ids):
                    log(f"  Progress: {i:,}/{len(app_ids):,}")

        df = pd.DataFrame(results)

        errors = df["error"].notna().sum()
        if errors:
            log(f"  {errors:,} fetch errors (deleted/inaccessible apps)", "WARN")

        has_exemptions = (df["exempt_count"] > 0).sum()
        log(f"  Apps with exemptions: {has_exemptions:,} / {len(df):,}")

        df.to_csv(cache, index=False)
        log(f"Saved → {cache}")

    log("═" * 60, "STEP")
    log(f"Total apps fetched:     {len(df):,}")
    log(f"Apps with exemptions:   {(df['exempt_count'].fillna(0) > 0).sum():,}")
    log(f"Total exemption rows:   {df['exempt_count'].fillna(0).sum():,.0f}")
    log("Next: run p3_build_audit.py", "STEP")
    log("═" * 60, "STEP")
