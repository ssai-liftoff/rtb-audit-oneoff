"""
Fraud Banking Audit — Full Runner

Runs all 5 parts in sequence:
  p1  Fetch IVT-tagged publisher accounts from Looker (all time)
  p2  Run 8 banking field matching passes against all other accounts
  p3  Fetch L60D spend data from Looker for the flagged accounts
  p4  Join everything into the audit CSV
  p5  Add dormant / previously-cleared review flags to final output
  p6  Flag low-confidence address matches (placeholder / shared addresses)

Usage:
  python3 -u fraud_banking_audit/run_all.py

To force re-fetch (ignore all cached outputs):
  python3 -u fraud_banking_audit/run_all.py --refresh
"""

import os
import sys
import subprocess
from datetime import datetime

SCRIPTS = [
    "fraud_banking_audit/p1_fetch_ivt_accounts.py",
    "fraud_banking_audit/p2_run_matching.py",
    "fraud_banking_audit/p3_fetch_spend.py",
    "fraud_banking_audit/p4_build_final.py",
    "fraud_banking_audit/p5_apply_review_flags.py",
    "fraud_banking_audit/p6_flag_address_quality.py",
]

CACHE_FILES = [
    "output/fraud_banking_audit/p1_ivt_accounts.csv",
    "output/fraud_banking_audit/p2_matches.csv",
    "output/fraud_banking_audit/p2_domain_review.csv",
    "output/fraud_banking_audit/p3_spend_data.csv",
    "output/fraud_banking_audit/p4_fraud_banking_audit.csv",
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

    log("=" * 60)
    log("FRAUD BANKING AUDIT — FULL RUN")
    log("=" * 60)

    if refresh:
        log("--refresh flag detected")
        clear_cache()

    start = datetime.now()

    for i, script in enumerate(SCRIPTS, 1):
        log("")
        log(f"── Step {i}/{len(SCRIPTS)}: {script} ──")
        log("")
        subprocess.run([sys.executable, "-u", script], check=True)

    elapsed = (datetime.now() - start).seconds // 60

    log("")
    log("=" * 60)
    log(f"ALL DONE in ~{elapsed} minutes")
    log(f"Final output: output/fraud_banking_audit/p4_fraud_banking_audit.csv")
    log(f"Domain review: output/fraud_banking_audit/p2_domain_review.csv")
    log("=" * 60)
