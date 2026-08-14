"""
RTB Platform Parity Audit — Part 2: Fetch Allow/Deny Lists

For each top RTB connection from Part 1, calls pub-ctrl-api to retrieve
allowedApplicationIds and deniedApplicationIds.

Input:  output/rtb_platform_parity_audit/p1_top_rtbs.csv
Output: output/rtb_platform_parity_audit/p2_connection_lists.csv
"""

import os
import json
import requests
import pandas as pd
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

OUTPUT_DIR = "output/rtb_platform_parity_audit"
RTB_CTRL_API_BASE = "https://pub-ctrl-api.vungle.com/api/v1/rtbconnections"

# Hardcoded — replace when expired (grab from pubadmin DevTools curl)
RTB_CTRL_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2NvdW50IjoiNjgxM2JmZDUxMDRiNDAwMDEx"
    "MzcyNDM2IiwiaXNNYW5hZ2VkQWNjb3VudCI6ZmFsc2UsImFwaVRva2VuIjoiN2FhMThlNTdmMTU0Nm"
    "Q2YmQ1NDJlMDc2YWI5NzI3YzMiLCJlbWFpbCI6InNzYWlAbGlmdG9mZi5pbyIsImZlYXR1cmVzIjpb"
    "ImZlYXR1cmVfbWVkaWF0aW9uX2FjY2VzcyJdLCJpZCI6IjY4MTNiZmQ1MTA0YjQwMDAxMTM3MjQzYS"
    "IsImlzX2RlbGV0ZWQiOmZhbHNlLCJpc0ludGVybmFsIjp0cnVlLCJyb2xlIjoiYWNjb3VudF9zdHJh"
    "dGVneSIsInNvdXJjZSI6IiIsInVpZCI6IjAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMCIsImV4cCI6MTc4"
    "NTQxODkxMn0.9kTUBJNtD1tXKx4UdI99KNWhYK-p-ABJDnKtWzvhMQQ"
)

MAX_WORKERS = 8

os.makedirs(OUTPUT_DIR, exist_ok=True)


def rtb_headers():
    return {
        "authorization": f"Bearer {RTB_CTRL_TOKEN}",
        "content-type": "application/json",
        "accept": "application/json, text/plain, */*",
        "vungle-source": "admin",
        "vungle-version": "1",
        "origin": "https://pubadmin.vungle.com",
        "referer": "https://pubadmin.vungle.com/",
    }


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def supported_platforms_label(allow_platform):
    """Human-readable list of RTB-supported platforms from API allowPlatform."""
    if not allow_platform:
        return "unknown"
    supported = [p for p in ("ios", "android", "amazon", "windows") if allow_platform.get(p)]
    return ",".join(supported) if supported else "none"


def fetch_connection_config(connection_id):
    try:
        resp = requests.get(
            f"{RTB_CTRL_API_BASE}/{connection_id}",
            headers=rtb_headers(),
            timeout=30,
        )
        if resp.status_code == 401:
            raise ValueError("RTB API token expired — update RTB_CTRL_TOKEN in p2_fetch_connection_lists.py")
        resp.raise_for_status()
        data = resp.json()

        allowed = data.get("allowedApplicationIds") or []
        denied = data.get("deniedApplicationIds") or []
        allow_platform = data.get("allowPlatform") or {}

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
            "allow_platform": json.dumps(allow_platform),
            "allow_platform_ios": bool(allow_platform.get("ios")),
            "allow_platform_android": bool(allow_platform.get("android")),
            "allow_platform_amazon": bool(allow_platform.get("amazon")),
            "allow_platform_windows": bool(allow_platform.get("windows")),
            "supported_platforms": supported_platforms_label(allow_platform),
            "error": None,
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
            "allow_platform": "{}",
            "allow_platform_ios": False,
            "allow_platform_android": False,
            "allow_platform_amazon": False,
            "allow_platform_windows": False,
            "supported_platforms": "unknown",
            "error": str(e),
        }


if __name__ == "__main__":
    import sys

    p1_path = f"{OUTPUT_DIR}/p1_top_rtbs.csv"
    cache = f"{OUTPUT_DIR}/p2_connection_lists.csv"

    if not os.path.exists(p1_path):
        raise FileNotFoundError(f"Not found: {p1_path} — run p1_fetch_top_rtbs.py first")

    refresh = "--refresh" in sys.argv
    if refresh and os.path.exists(cache):
        os.remove(cache)
        log("Removed cached p2 output (--refresh)", "WARN")

    if os.path.exists(cache):
        log(f"Cache found — loading {cache}")
        out = pd.read_csv(cache)
    else:
        log("═" * 60, "STEP")
        log("RTB PLATFORM PARITY — PART 2: FETCH CONNECTION LISTS", "STEP")
        log("═" * 60, "STEP")

        p1 = pd.read_csv(p1_path)
        connection_ids = p1["rtb_connection_id"].dropna().astype(str).unique().tolist()
        log(f"Fetching allow/deny lists for {len(connection_ids)} connections...")

        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(fetch_connection_config, cid): cid for cid in connection_ids}
            for i, future in enumerate(as_completed(futures), 1):
                results.append(future.result())
                if i % 5 == 0 or i == len(connection_ids):
                    log(f"  Progress: {i}/{len(connection_ids)}")

        configs = pd.DataFrame(results)
        out = p1.merge(configs, on="rtb_connection_id", how="left")
        out.to_csv(cache, index=False)
        log(f"Saved → {cache}")

    if "supported_platforms" in out.columns:
        log(f"Platform support from API: {out['supported_platforms'].value_counts().to_dict()}")

    allow_ct = (out["list_type"] == "allow").sum()
    deny_ct = (out["list_type"] == "deny").sum()
    none_ct = (out["list_type"] == "none").sum()
    err_ct = (out["list_type"] == "error").sum()

    log("═" * 60, "STEP")
    log(f"Allow-list connections: {allow_ct}")
    log(f"Deny-list connections:  {deny_ct}")
    log(f"No list:                {none_ct}")
    if err_ct:
        log(f"Errors:                 {err_ct}", "WARN")
    log("Next: run p3_fetch_app_portfolios.py", "STEP")
    log("═" * 60, "STEP")
