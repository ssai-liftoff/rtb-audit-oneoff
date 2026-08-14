"""
SMB RTB App Blocklist Audit — Part 3: Fetch RTB Connection Allow/Deny Lists

For each qualifying RTB connection (spend > $1k/day) from Part 2,
calls the RTB connection API to retrieve allowedApplicationIds and
deniedApplicationIds. Results are saved per-connection for Part 4.

Input:  output/smb_rtb_audit/p2_qualifying_accounts.csv
Output: output/smb_rtb_audit/p3_connection_configs.csv
"""

import os
import json
import requests
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

OUTPUT_DIR = "output/smb_rtb_audit"
os.makedirs(OUTPUT_DIR, exist_ok=True)

RTB_CTRL_API_BASE = "https://pub-ctrl-api.vungle.com/api/v1/rtbconnections"

# Hardcoded — token has a fixed expiry, replace when it expires
RTB_CTRL_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2NvdW50IjoiNjgxM2JmZDUxMDRiNDAwMDExMzcyNDM2IiwiaXNNYW5hZ2VkQWNjb3VudCI6ZmFsc2UsImFwaVRva2VuIjoiN2FhMThlNTdmMTU0NmQ2YmQ1NDJlMDc2YWI5NzI3YzMiLCJlbWFpbCI6InNzYWlAbGlmdG9mZi5pbyIsImZlYXR1cmVzIjpbImZlYXR1cmVfbWVkaWF0aW9uX2FjY2VzcyJdLCJpZCI6IjY4MTNiZmQ1MTA0YjQwMDAxMTM3MjQzYSIsImlzX2RlbGV0ZWQiOmZhbHNlLCJpc0ludGVybmFsIjp0cnVlLCJyb2xlIjoiYWNjb3VudF9zdHJhdGVneSIsInNvdXJjZSI6IiIsInVpZCI6IjAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMCIsImV4cCI6MTc3OTQ2NDcxNn0.p2ay68H-bT50vhUUtnvFQDBRPySVirRzGhck2eXo6-8"

RTB_CTRL_HEADERS = {
    "authorization": f"Bearer {RTB_CTRL_TOKEN}",
    "content-type": "application/json",
    "accept": "application/json, text/plain, */*",
    "vungle-source": "admin",
    "vungle-version": "1",
    "origin": "https://pubadmin.vungle.com",
    "referer": "https://pubadmin.vungle.com/"
}

SPEND_THRESHOLD_DAILY = 1000
MAX_WORKERS = 8


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def fetch_connection_config(connection_id):
    try:
        resp = requests.get(
            f"{RTB_CTRL_API_BASE}/{connection_id}",
            headers=RTB_CTRL_HEADERS,
            timeout=15
        )
        if resp.status_code == 401:
            raise ValueError("RTB ctrl API token expired — update RTB_CTRL_TOKEN in p3_fetch_connection_lists.py")
        resp.raise_for_status()
        data = resp.json()

        allowed = data.get("allowedApplicationIds") or []
        denied = data.get("deniedApplicationIds") or []

        if allowed:
            list_type = "allow"
        elif denied:
            list_type = "deny"
        else:
            list_type = "none"

        return {
            "rtb_connection_id": connection_id,
            "list_type": list_type,
            "allowed_app_ids": json.dumps(allowed),
            "denied_app_ids": json.dumps(denied),
            "allow_count": len(allowed),
            "deny_count": len(denied),
            "error": None
        }

    except ValueError:
        raise
    except Exception as e:
        return {
            "rtb_connection_id": connection_id,
            "list_type": "error",
            "allowed_app_ids": "[]",
            "denied_app_ids": "[]",
            "allow_count": 0,
            "deny_count": 0,
            "error": str(e)
        }


if __name__ == "__main__":
    p2_path = f"{OUTPUT_DIR}/p2_qualifying_accounts.csv"
    if not os.path.exists(p2_path):
        raise FileNotFoundError(f"Part 2 output not found: {p2_path} — run p2_filter_rtb_accounts.py first")

    log("═" * 55, "STEP")
    log("SMB RTB AUDIT — PART 3: FETCH CONNECTION LISTS", "STEP")
    log("═" * 55, "STEP")

    df = pd.read_csv(p2_path)

    # Only scan connections that are themselves above the daily spend threshold
    qualifying = (
        df[df["daily_spend"] >= SPEND_THRESHOLD_DAILY]["rtb_connection_id"]
        .dropna().astype(str).unique().tolist()
    )
    log(f"Qualifying connections (≥ ${SPEND_THRESHOLD_DAILY}/day): {len(qualifying)}")

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_connection_config, cid): cid for cid in qualifying}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            results.append(result)
            if i % 25 == 0 or i == len(qualifying):
                log(f"  Progress: {i}/{len(qualifying)}")

    configs_df = pd.DataFrame(results)

    allow_ct = (configs_df["list_type"] == "allow").sum()
    deny_ct = (configs_df["list_type"] == "deny").sum()
    none_ct = (configs_df["list_type"] == "none").sum()
    err_ct = (configs_df["list_type"] == "error").sum()

    log(f"  Connections with allow list: {allow_ct}")
    log(f"  Connections with deny list:  {deny_ct}")
    log(f"  Connections with no list:    {none_ct}")
    if err_ct:
        log(f"  Errors:                      {err_ct}", "WARN")

    output_path = f"{OUTPUT_DIR}/p3_connection_configs.csv"
    configs_df.to_csv(output_path, index=False)
    log(f"Saved → {output_path}")
    log("═" * 55, "STEP")
    log("PART 3 COMPLETE", "STEP")
    log("Next: run p4_build_audit.py", "STEP")
    log("═" * 55, "STEP")
