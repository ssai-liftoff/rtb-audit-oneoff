"""
Kalshi / Polymarket Parity Audit — Full Runner

Usage:
  python3 -u kalshi_polymarket_parity_audit/run_all.py

To force re-fetch (ignore caches):
  python3 -u kalshi_polymarket_parity_audit/run_all.py --refresh
"""

import os
import subprocess
import sys
from datetime import datetime

SCRIPTS = [
    "kalshi_polymarket_parity_audit/p1_fetch_supply_blocks.py",
    "kalshi_polymarket_parity_audit/p2_fetch_demand_blocks.py",
    "kalshi_polymarket_parity_audit/p3_build_audit.py",
]

CACHE_FILES = [
    "output/kalshi_polymarket_parity_audit/p1_supply_blocks.csv",
    "output/kalshi_polymarket_parity_audit/p2_demand_blocks.csv",
    "output/kalshi_polymarket_parity_audit/p3_kalshi_gap_audit.csv",
    "output/kalshi_polymarket_parity_audit/p3_polymarket_gap_audit.csv",
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
        print(f"[{ts()}] --refresh: deleting cached files...", flush=True)
        for path in CACHE_FILES:
            if os.path.exists(path):
                os.remove(path)
                print(f"  deleted {path}", flush=True)

    print(f"\n[{ts()}] ══ KALSHI / POLYMARKET PARITY AUDIT ══", flush=True)
    for script in SCRIPTS:
        run_script(script)

    print(f"\n[{ts()}] ══ ALL PARTS COMPLETE ══", flush=True)
    print("Outputs:", flush=True)
    print("  output/kalshi_polymarket_parity_audit/p3_kalshi_gap_audit.csv", flush=True)
    print("  output/kalshi_polymarket_parity_audit/p3_polymarket_gap_audit.csv", flush=True)
