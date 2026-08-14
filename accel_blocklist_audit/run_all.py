"""
Accelerate Blocklist Audit — Full Runner

Runs all 4 parts in sequence.

Usage:
  python3 -u accel_blocklist_audit/run_all.py

To force re-fetch all data (ignore cached CSVs):
  python3 -u accel_blocklist_audit/run_all.py --refresh
"""

import os
import sys
import subprocess
from datetime import datetime

SCRIPTS = [
    "accel_blocklist_audit/p1_fetch_demand_spot.py",
    "accel_blocklist_audit/p2_fetch_supply_spend.py",
    "accel_blocklist_audit/p3_fetch_blocklist.py",
    "accel_blocklist_audit/p4_build_audit.py",
]

CACHE_FILES = [
    "output/accel_blocklist_audit/p1_demand_spot.csv",
    "output/accel_blocklist_audit/p2_supply_spend.csv",
    "output/accel_blocklist_audit/p3_blocklist.csv",
    "output/accel_blocklist_audit/p3_level_global.csv",
    "output/accel_blocklist_audit/p3_level_customer.csv",
    "output/accel_blocklist_audit/p3_level_campaign_group.csv",
    "output/accel_blocklist_audit/p3_level_campaign.csv",
    "output/accel_blocklist_audit/p3_level_advertiser_app.csv",
    "output/accel_blocklist_audit/p4_accel_blocklist_audit.csv",
]

OUTPUT_FILES = [
    "output/accel_blocklist_audit/p4_accel_blocklist_audit.csv",
]


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def clear_cache():
    log("Clearing all cached files...")
    for f in CACHE_FILES:
        if os.path.exists(f):
            os.remove(f)
            log(f"  Deleted {f}")
    log("Cache cleared.")


if __name__ == "__main__":
    refresh = "--refresh" in sys.argv

    log("=" * 55)
    log("ACCEL BLOCKLIST AUDIT — FULL RUN")
    log("=" * 55)

    if refresh:
        log("--refresh flag detected")
        clear_cache()

    start = datetime.now()

    for i, script in enumerate(SCRIPTS, 1):
        log("")
        log(f"-- Step {i}/{len(SCRIPTS)}: {script} --")
        log("")
        subprocess.run([sys.executable, "-u", script], check=True)

    elapsed = int((datetime.now() - start).total_seconds() // 60)

    log("")
    log("=" * 55)
    log(f"ALL DONE in ~{elapsed} minutes")
    log("Output files:")
    for f in OUTPUT_FILES:
        if os.path.exists(f):
            log(f"  {f}")
    log("=" * 55)
