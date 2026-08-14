"""
SMB RTB App Blocklist Audit — Part 2: Filter RTB Accounts via API

For each RTB account from Part 1, calls the GraphQL account API to check
blockUnmanagedPublisherRequest. Accounts where this is True are entirely
skipped — they blanket-block unmanaged/SMB publisher traffic.

Input:  output/smb_rtb_audit/p1_rtb_accounts_connections.csv
Output: output/smb_rtb_audit/p2_qualifying_accounts.csv
"""

import os
import time
import requests
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

OUTPUT_DIR = "output/smb_rtb_audit"
os.makedirs(OUTPUT_DIR, exist_ok=True)

GRAPHQL_URL = "https://pub-gateway-api.vungle.com/query"

# Hardcoded — token has a fixed expiry, replace when it expires
ACCOUNT_API_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2NvdW50IjoiNjgxM2JmZDUxMDRiNDAwMDExMzcyNDM2IiwiaXNNYW5hZ2VkQWNjb3VudCI6ZmFsc2UsImFwaVRva2VuIjoiN2FhMThlNTdmMTU0NmQ2YmQ1NDJlMDc2YWI5NzI3YzMiLCJlbWFpbCI6InNzYWlAbGlmdG9mZi5pbyIsImZlYXR1cmVzIjpbImZlYXR1cmVfbWVkaWF0aW9uX2FjY2VzcyJdLCJpZCI6IjY4MTNiZmQ1MTA0YjQwMDAxMTM3MjQzYSIsImlzX2RlbGV0ZWQiOmZhbHNlLCJpc0ludGVybmFsIjp0cnVlLCJyb2xlIjoiYWNjb3VudF9zdHJhdGVneSIsInNvdXJjZSI6IiIsInVpZCI6IjAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMCIsImV4cCI6MTc3OTQ2MzI2NH0.ZDWwoCqhrFH7wqOPjCABP04hV24UI2tfKYeVwLOdvV0"

GRAPHQL_HEADERS = {
    "authorization": f"Bearer {ACCOUNT_API_TOKEN}",
    "content-type": "application/json",
    "accept": "application/json, text/plain, */*",
    "vungle-source": "admin",
    "vungle-version": "1",
    "origin": "https://pubadmin.vungle.com",
    "referer": "https://pubadmin.vungle.com/"
}

# Minimal query — only fetching the one flag we need
ACCOUNT_QUERY = """
query account($id: String!) {
  account(id: $id) {
    id
    name
    blockUnmanagedPublisherRequest
  }
}
"""

MAX_WORKERS = 5


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def fetch_account_config(account_id):
    try:
        resp = requests.post(
            GRAPHQL_URL,
            headers=GRAPHQL_HEADERS,
            json={
                "operationName": "account",
                "variables": {"id": str(account_id)},
                "query": ACCOUNT_QUERY
            },
            timeout=15
        )
        if resp.status_code == 401:
            raise ValueError("Account API token expired — update ACCOUNT_API_TOKEN in p2_filter_rtb_accounts.py")
        resp.raise_for_status()

        account = resp.json().get("data", {}).get("account") or {}
        if not account:
            return {"account_id": account_id, "block_unmanaged": None, "error": "empty response"}

        return {
            "account_id": account_id,
            "block_unmanaged": account.get("blockUnmanagedPublisherRequest"),
            "error": None
        }

    except ValueError:
        raise
    except Exception as e:
        return {"account_id": account_id, "block_unmanaged": None, "error": str(e)}


if __name__ == "__main__":
    p1_path = f"{OUTPUT_DIR}/p1_rtb_accounts_connections.csv"
    if not os.path.exists(p1_path):
        raise FileNotFoundError(f"Part 1 output not found: {p1_path} — run p1_fetch_looker.py first")

    log("═" * 55, "STEP")
    log("SMB RTB AUDIT — PART 2: FILTER RTB ACCOUNTS", "STEP")
    log("═" * 55, "STEP")

    df = pd.read_csv(p1_path)
    account_ids = df["rtb_account_id"].dropna().astype(str).unique().tolist()
    log(f"Checking {len(account_ids)} RTB accounts for blockUnmanagedPublisherRequest...")

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_account_config, aid): aid for aid in account_ids}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            if result["error"]:
                status = f"error: {result['error']}"
            elif result["block_unmanaged"] is True:
                status = "blockUnmanaged=true → SKIP"
            elif result["block_unmanaged"] is False:
                status = "blockUnmanaged=false → keep"
            else:
                status = "blockUnmanaged=null → keep (unknown)"
            log(f"  [{i}/{len(account_ids)}] {result['account_id']} → {status}")

    results_df = pd.DataFrame(results)

    skipped = results_df[results_df["block_unmanaged"] == True]["account_id"].tolist()
    log(f"\nAccounts skipped (blockUnmanaged=true): {len(skipped)}", "WARN" if skipped else "INFO")
    log(f"Accounts proceeding to Part 3: {len(account_ids) - len(skipped)}")

    # Drop all connections under skipped accounts
    qualifying_df = df[~df["rtb_account_id"].astype(str).isin(skipped)].copy()

    # Tag each row with the check result for auditability
    block_map = results_df.set_index("account_id")["block_unmanaged"].to_dict()
    qualifying_df["block_unmanaged_check"] = (
        qualifying_df["rtb_account_id"].astype(str).map(block_map)
    )

    output_path = f"{OUTPUT_DIR}/p2_qualifying_accounts.csv"
    qualifying_df.to_csv(output_path, index=False)
    log(f"Saved → {output_path}")
    log(f"  Qualifying RTB accounts:    {qualifying_df['rtb_account_id'].nunique()}", "STEP")
    log(f"  Qualifying RTB connections: {qualifying_df['rtb_connection_id'].nunique()}", "STEP")
    log("═" * 55, "STEP")
    log("PART 2 COMPLETE", "STEP")
    log("Next: run p3_fetch_connection_lists.py", "STEP")
    log("═" * 55, "STEP")
