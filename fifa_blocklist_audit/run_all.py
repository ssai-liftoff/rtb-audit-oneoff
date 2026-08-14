"""
FIFA Blocklist Audit — Full Runner

Runs all 4 parts in sequence. Each part caches its output and skips re-fetching
if the cache already exists.

Usage:
  python3 -u fifa_blocklist_audit/run_all.py

To force re-fetch all data (ignore all caches):
  python3 -u fifa_blocklist_audit/run_all.py --refresh
"""

import os
import sys
import subprocess
from datetime import datetime

SCRIPTS = [
    "fifa_blocklist_audit/p1_fetch_supply.py",
    "fifa_blocklist_audit/p2_fetch_advertiser_profiles.py",
    "fifa_blocklist_audit/p3_fetch_api_blocklists.py",
    "fifa_blocklist_audit/p4_build_audit.py",
]

CACHE_FILES = [
    "output/fifa_blocklist_audit/p1_supply_apps.csv",
    "output/fifa_blocklist_audit/p1_supply_accounts.csv",
    "output/fifa_blocklist_audit/p2_advertiser_profiles.csv",
    "output/fifa_blocklist_audit/p3_account_blocklists.csv",
    "output/fifa_blocklist_audit/p3_app_blocklists.csv",
    "output/fifa_blocklist_audit/p4_fifa_blocklist_audit.csv",
]


def ts():
    return datetime.now().strftime("%H:%M:%S")


def run_script(script):
    print(f"\n[{ts()}] ── Running {script} {'─' * max(0, 50 - len(script))}", flush=True)
    result = subprocess.run([sys.executable, "-u", script], check=False)
    if result.returncode != 0:
        print(f"\n[{ts()}] ✗ {script} failed with exit code {result.returncode}", flush=True)
        sys.exit(result.returncode)


if __name__ == "__main__":
    refresh = "--refresh" in sys.argv

    if refresh:
        print(f"[{ts()}] --refresh: deleting all cached files...", flush=True)
        for f in CACHE_FILES:
            if os.path.exists(f):
                os.remove(f)
                print(f"  deleted {f}", flush=True)

    print(f"\n[{ts()}] ══ FIFA BLOCKLIST AUDIT ══", flush=True)
    for script in SCRIPTS:
        run_script(script)

    print(f"\n[{ts()}] ══ ALL PARTS COMPLETE ══", flush=True)
    print(f"Final output: output/fifa_blocklist_audit/p4_fifa_blocklist_audit.csv", flush=True)
