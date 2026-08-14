"""
Low Share of Wallet Analysis - Full Runner
Runs all 4 parts in sequence.

Usage:
  python3 -u low_sov_analysis/run_all.py

To force re-fetch all data (ignore cache):
  python3 -u low_sov_analysis/run_all.py --refresh
"""

import os
import sys
import subprocess
from datetime import datetime

SCRIPTS = [
    "low_sov_analysis/low_sov_part1_base_spend.py",
    "low_sov_analysis/low_sov_part2_rankings.py",
    "low_sov_analysis/low_sov_part3_creative_split.py",
    "low_sov_analysis/low_sov_part4_final.py",
]

CACHE_FILES = [
    "output/low_sov_analysis/raw_demand_90d.csv",
    "output/low_sov_analysis/raw_demand_30d.csv",
    "output/low_sov_analysis/raw_supply_metadata.csv",
    "output/low_sov_analysis/raw_exchange_90d.csv",
    "output/low_sov_analysis/raw_exchange_30d.csv",
    "output/low_sov_analysis/raw_creative_90d.csv",
    "output/low_sov_analysis/raw_creative_30d.csv",
    "output/low_sov_analysis/p1_base_spend.csv",
    "output/low_sov_analysis/p2_rankings.csv",
    "output/low_sov_analysis/p3_creative_split.csv",
    "output/low_sov_analysis/low_sov_final.csv",
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
    log("LOW SHARE OF WALLET ANALYSIS - FULL RUN")
    log("=" * 55)

    if refresh:
        log("--refresh flag detected")
        clear_cache()

    start = datetime.now()

    for i, script in enumerate(SCRIPTS, 1):
        log(f"")
        log(f"-- Step {i}/4: {script} --")
        log(f"")
        result = subprocess.run(
            [sys.executable, "-u", script],
            check=True
        )

    end = datetime.now()
    elapsed = (end - start).seconds // 60

    log("")
    log("=" * 55)
    log(f"ALL DONE in ~{elapsed} minutes")
    log(f"Final output: output/low_sov_analysis/low_sov_final.csv")
    log("=" * 55)