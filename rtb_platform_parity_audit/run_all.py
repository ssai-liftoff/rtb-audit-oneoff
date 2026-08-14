"""
RTB Platform Parity Audit — Run All Parts

Usage:
  python3 -u rtb_platform_parity_audit/run_all.py

To force re-fetch (ignore caches):
  python3 -u rtb_platform_parity_audit/run_all.py --refresh
"""

import os
import sys
import subprocess
from datetime import datetime

SCRIPTS = [
    "rtb_platform_parity_audit/p1_fetch_top_rtbs.py",
    "rtb_platform_parity_audit/p2_fetch_connection_lists.py",
    "rtb_platform_parity_audit/p3_fetch_app_portfolios.py",
    "rtb_platform_parity_audit/p4_match_siblings.py",
    "rtb_platform_parity_audit/p5_build_audit.py",
    "rtb_platform_parity_audit/p6_fetch_combo_spend.py",
]

CACHE_FILES = [
    "output/rtb_platform_parity_audit/p1_top_rtbs.csv",
    "output/rtb_platform_parity_audit/p2_connection_lists.csv",
    "output/rtb_platform_parity_audit/p3_listed_apps.csv",
    "output/rtb_platform_parity_audit/p3_account_portfolios.csv",
    "output/rtb_platform_parity_audit/p4_sibling_pairs.csv",
    "output/rtb_platform_parity_audit/p5_allow_gaps.csv",
    "output/rtb_platform_parity_audit/p5_deny_gaps.csv",
    "output/rtb_platform_parity_audit/p5_platform_parity_audit.csv",
    "output/rtb_platform_parity_audit/p6_combo_spend_raw.csv",
    "output/rtb_platform_parity_audit/p6_platform_parity_audit.csv",
]


def ts():
    return datetime.now().strftime("%H:%M:%S")


def run_script(script):
    print(f"\n[{ts()}] ── Running {script} {'─' * max(0, 45 - len(script))}", flush=True)
    result = subprocess.run([sys.executable, "-u", script], check=False)
    if result.returncode != 0:
        print(f"\n[{ts()}] ✗ {script} failed with exit code {result.returncode}", flush=True)
        sys.exit(result.returncode)


if __name__ == "__main__":
    refresh = "--refresh" in sys.argv

    if refresh:
        print(f"[{ts()}] --refresh: deleting cached files...", flush=True)
        for f in CACHE_FILES:
            if os.path.exists(f):
                os.remove(f)
                print(f"  deleted {f}", flush=True)

    print(f"\n[{ts()}] ══ RTB PLATFORM PARITY AUDIT ══", flush=True)
    for script in SCRIPTS:
        run_script(script)

    print(f"\n[{ts()}] ══ ALL PARTS COMPLETE ══", flush=True)
    print("Final output: output/rtb_platform_parity_audit/p6_platform_parity_audit.csv", flush=True)
