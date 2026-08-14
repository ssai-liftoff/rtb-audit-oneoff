"""
Fraud Banking Audit — Part 6: Flag low-confidence address and email-domain matches

Usage:
  python3 -u fraud_banking_audit/p6_flag_address_quality.py
  python3 -u fraud_banking_audit/p6_flag_address_quality.py path/to/review.csv
"""

import os
import sys
import pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from match_quality import (
    assess_address_row,
    assess_email_domain_row,
    build_banking_address_counts,
    build_banking_email_domain_counts,
    load_ivt_domain_counts,
)

DEFAULT_INPUT = "output/fraud_banking_audit/p4_fraud_banking_audit.csv"
DEFAULT_OUTPUT = DEFAULT_INPUT
BANKING_CSV = "fraud_banking_audit/data/pub_banking_data.csv"
DOMAIN_REVIEW_CSV = "output/fraud_banking_audit/p2_domain_review.csv"

COL_MATCH_TYPES = "matched fraud entity?"
COL_ENTITY_VALUE = "matched fraud entity value"
COL_ADDRESS_FLAG = "Is address match low confidence?"
COL_ADDRESS_REASON = "Address low confidence reason"
COL_EMAIL_FLAG = "Is email domain match low confidence?"
COL_EMAIL_CONFIDENCE = "Email domain match confidence"
COL_EMAIL_REASON = "Email domain low confidence reason"


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def enrich_dataframe(df, banking_address_counts, banking_domain_counts, ivt_domain_counts):
    df = df.copy()
    address_flags, address_reasons = [], []
    email_flags, email_confidence, email_reasons = [], [], []

    for _, row in df.iterrows():
        match_types = row.get(COL_MATCH_TYPES, row.get("matched_on", ""))
        entity_value = row.get(COL_ENTITY_VALUE, row.get("matched_fraud_entity", ""))

        addr_flagged, addr_reason = assess_address_row(
            match_types, entity_value, banking_address_counts
        )
        email_flagged, email_conf, email_reason = assess_email_domain_row(
            match_types, entity_value, ivt_domain_counts, banking_domain_counts
        )

        address_flags.append("TRUE" if addr_flagged else "FALSE")
        address_reasons.append(addr_reason)
        email_flags.append("TRUE" if email_flagged else "FALSE")
        email_confidence.append(email_conf)
        email_reasons.append(email_reason)

    for col in [
        COL_ADDRESS_FLAG, COL_ADDRESS_REASON,
        COL_EMAIL_FLAG, COL_EMAIL_CONFIDENCE, COL_EMAIL_REASON,
    ]:
        if col in df.columns:
            df = df.drop(columns=[col])

    df[COL_ADDRESS_FLAG] = address_flags
    df[COL_ADDRESS_REASON] = address_reasons
    df[COL_EMAIL_FLAG] = email_flags
    df[COL_EMAIL_CONFIDENCE] = email_confidence
    df[COL_EMAIL_REASON] = email_reasons
    return df


if __name__ == "__main__":
    input_csv = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
    output_csv = sys.argv[2] if len(sys.argv) > 2 else input_csv

    if not os.path.exists(input_csv):
        raise FileNotFoundError(f"Input not found: {input_csv}")
    if not os.path.exists(BANKING_CSV):
        raise FileNotFoundError(f"Banking CSV not found: {BANKING_CSV}")
    if not os.path.exists(DOMAIN_REVIEW_CSV):
        raise FileNotFoundError(f"Domain review CSV not found: {DOMAIN_REVIEW_CSV}")

    log("=" * 60, "STEP")
    log("FRAUD BANKING AUDIT — PART 6: MATCH QUALITY FLAGS", "STEP")
    log("=" * 60, "STEP")

    df = pd.read_csv(input_csv, dtype=str).fillna("")
    log(f"Loaded {len(df):,} rows from {input_csv}")

    log("Building lookup maps...", "STEP")
    banking_address_counts = build_banking_address_counts(BANKING_CSV)
    banking_domain_counts = build_banking_email_domain_counts(BANKING_CSV)
    ivt_domain_counts = load_ivt_domain_counts(DOMAIN_REVIEW_CSV)
    log(f"  Unique addresses: {len(banking_address_counts):,}")
    log(f"  Unique email domains in banking data: {len(banking_domain_counts):,}")
    log(f"  IVT email domains: {len(ivt_domain_counts):,}")

    df = enrich_dataframe(df, banking_address_counts, banking_domain_counts, ivt_domain_counts)
    df.to_csv(output_csv, index=False)
    log(f"Saved → {output_csv}")

    address_rows = df[df[COL_MATCH_TYPES].str.contains("address", case=False, na=False)]
    email_rows = df[df[COL_MATCH_TYPES].str.contains("email_domain", case=False, na=False)]

    log("=" * 60, "STEP")
    log("PART 6 COMPLETE — SUMMARY", "STEP")
    log(f"  Address matches:                   {len(address_rows):>5,}", "STEP")
    log(f"    ↳ low confidence:                {(address_rows[COL_ADDRESS_FLAG] == 'TRUE').sum():>5,}", "STEP")
    log(f"  Email domain matches:              {len(email_rows):>5,}", "STEP")
    log(f"    ↳ low confidence:                {(email_rows[COL_EMAIL_FLAG] == 'TRUE').sum():>5,}", "STEP")
    for level in ("HIGH", "MEDIUM", "LOW"):
        count = (email_rows[COL_EMAIL_CONFIDENCE] == level).sum()
        log(f"    ↳ confidence {level:<6}:            {count:>5,}", "STEP")
    log("=" * 60, "STEP")
