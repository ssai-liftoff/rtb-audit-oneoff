"""
Moloco RTB Parity Audit — Full Runner

Usage:
  python3 -u moloco_parity_audit/run_all.py
  python3 -u moloco_parity_audit/run_all.py --refresh
"""

import os
import subprocess
import sys
from datetime import datetime

SCRIPTS = [
    "moloco_parity_audit/p1_fetch_supply_blocks.py",
    "moloco_parity_audit/p2_build_audit.py",
]

CACHE_FILES = [
    "output/moloco_parity_audit/p1_supply_blocks.csv",
    "output/moloco_parity_audit/p2_kalshi_gap_audit.csv",
    "output/moloco_parity_audit/p2_polymarket_gap_audit.csv",
    "output/moloco_parity_audit/p2_combined_gap_audit.csv",
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
    if "--refresh" in sys.argv:
        print(f"[{ts()}] --refresh: deleting cached files...", flush=True)
        for path in CACHE_FILES:
            if os.path.exists(path):
                os.remove(path)
                print(f"  deleted {path}", flush=True)

    print(f"\n[{ts()}] ══ MOLOCO PARITY AUDIT ══", flush=True)
    for script in SCRIPTS:
        run_script(script)

    print(f"\n[{ts()}] ══ ALL PARTS COMPLETE ══", flush=True)
    print("  output/moloco_parity_audit/p2_combined_gap_audit.csv", flush=True)
