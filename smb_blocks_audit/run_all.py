"""
SMB Blocks Audit — Full Runner

Runs all 3 parts in sequence.

Usage:
  python3 -u smb_blocks_audit/run_all.py

To force re-fetch all data (ignore cached CSVs):
  python3 -u smb_blocks_audit/run_all.py --refresh
"""

import os
import sys
import subprocess
from datetime import datetime

SCRIPTS = [
    "smb_blocks_audit/p1_fetch_smb_blocks.py",
    "smb_blocks_audit/p2_fetch_network_spend.py",
    "smb_blocks_audit/p3_build_audit.py",
]

CACHE_FILES = [
    "output/smb_blocks_audit/p1_smb_blocks.csv",
    "output/smb_blocks_audit/p2_network_total.csv",
    "output/smb_blocks_audit/p2_network_iab.csv",
    "output/smb_blocks_audit/p2_network_rtb_accounts.csv",
    "output/smb_blocks_audit/p2_network_rtb_connections.csv",
    "output/smb_blocks_audit/p3_iab_audit.csv",
    "output/smb_blocks_audit/p3_rtb_accounts_audit.csv",
    "output/smb_blocks_audit/p3_rtb_connections_audit.csv",
]

OUTPUT_FILES = [
    "output/smb_blocks_audit/p3_iab_audit.csv",
    "output/smb_blocks_audit/p3_rtb_accounts_audit.csv",
    "output/smb_blocks_audit/p3_rtb_connections_audit.csv",
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
    log("SMB BLOCKS AUDIT — FULL RUN")
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
