"""
Whitelist Audit — Run All Parts

Runs p1 → p2 → p3 in sequence.

Usage:
    python3 whitelist_audit/run_all.py

Each part caches its output. Re-running skips already-completed steps.
Delete the relevant output file to force a re-fetch.
"""

import subprocess
import sys
from datetime import datetime


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def run(script):
    log(f"Running {script}...", "STEP")
    result = subprocess.run([sys.executable, script], check=True)
    return result.returncode


if __name__ == "__main__":
    steps = [
        "whitelist_audit/p1_fetch_supply.py",
        "whitelist_audit/p2_fetch_whitelists.py",
        "whitelist_audit/p3_build_audit.py",
        "whitelist_audit/p4_fetch_combo_spend.py",
    ]

    log("═" * 60, "STEP")
    log("WHITELIST AUDIT — RUN ALL", "STEP")
    log("═" * 60, "STEP")

    for step in steps:
        run(step)
        print()

    log("═" * 60, "STEP")
    log("All parts complete. Final output: output/whitelist_audit/p4_whitelist_audit.csv", "STEP")
    log("═" * 60, "STEP")
