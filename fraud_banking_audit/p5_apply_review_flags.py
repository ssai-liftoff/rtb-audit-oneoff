"""
Fraud Banking Audit — Part 5: Apply Review Flags

Adds two false-positive review columns to the final audit output:
  - Is matched pubs dormant?       — TRUE if ALL matched IVT pub IDs are on the
                                     dormant closure masterlist
  - Is previously marked as "No"?  — TRUE if new_fraud_pub_id was previously
                                     reviewed and cleared

Inputs:
  - output/fraud_banking_audit/p4_fraud_banking_audit.csv
  - fraud_banking_audit/data/dormant_closure_masterlist.csv
  - fraud_banking_audit/data/previously_marked_no.csv

Outputs:
  - output/fraud_banking_audit/p4_fraud_banking_audit.csv  (enriched in place)
"""

import os
import pandas as pd
from datetime import datetime

OUTPUT_DIR = "output/fraud_banking_audit"
INPUT_CSV = f"{OUTPUT_DIR}/p4_fraud_banking_audit.csv"
DORMANT_CSV = "fraud_banking_audit/data/dormant_closure_masterlist.csv"
PREVIOUSLY_NO_CSV = "fraud_banking_audit/data/previously_marked_no.csv"

COL_DORMANT = "Is matched pubs dormant?"
COL_PREVIOUSLY_NO = 'Is previously marked as "No"?'
COL_PUB_ID = "potential fraud pub id"
COL_MATCHED_IVT_IDS = "matched fraud pub ids"


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def load_id_set(path, column):
    df = pd.read_csv(path, dtype=str).fillna("")
    ids = set(df[column].astype(str).str.strip()) - {""}
    return ids


def parse_matched_ivt_ids(value):
    text = str(value).strip()
    if not text or text.upper() == "N/A":
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def all_matched_pubs_dormant(matched_ids, dormant_ids):
    if not matched_ids:
        return False
    return all(pub_id in dormant_ids for pub_id in matched_ids)


if __name__ == "__main__":
    log("=" * 60, "STEP")
    log("FRAUD BANKING AUDIT — PART 5: APPLY REVIEW FLAGS", "STEP")
    log("=" * 60, "STEP")

    if not os.path.exists(INPUT_CSV):
        raise FileNotFoundError(f"Final audit not found at {INPUT_CSV}. Run p4 first.")

    df = pd.read_csv(INPUT_CSV, dtype=str).fillna("")
    log(f"Loaded {len(df):,} rows from Part 4")

    dormant_ids = load_id_set(DORMANT_CSV, "Publisher Account ID")
    previously_no_ids = load_id_set(PREVIOUSLY_NO_CSV, "account_id")
    log(f"Dormant closure masterlist: {len(dormant_ids):,} pub IDs")
    log(f"Previously marked No list:  {len(previously_no_ids):,} pub IDs")

    df[COL_DORMANT] = df[COL_MATCHED_IVT_IDS].apply(
        lambda val: "TRUE" if all_matched_pubs_dormant(parse_matched_ivt_ids(val), dormant_ids) else "FALSE"
    )
    df[COL_PREVIOUSLY_NO] = df[COL_PUB_ID].apply(
        lambda pub_id: "TRUE" if str(pub_id).strip() in previously_no_ids else "FALSE"
    )

    review_cols = [c for c in df.columns if c not in (COL_DORMANT, COL_PREVIOUSLY_NO)]
    df = df[review_cols + [COL_DORMANT, COL_PREVIOUSLY_NO]]

    df.to_csv(INPUT_CSV, index=False)
    log(f"Review flags saved → {INPUT_CSV}")

    dormant_true = (df[COL_DORMANT] == "TRUE").sum()
    previously_no_true = (df[COL_PREVIOUSLY_NO] == "TRUE").sum()
    both_true = ((df[COL_DORMANT] == "TRUE") & (df[COL_PREVIOUSLY_NO] == "TRUE")).sum()

    log("=" * 60, "STEP")
    log("PART 5 COMPLETE — SUMMARY", "STEP")
    log(f"  {COL_DORMANT:<35} {dormant_true:>5,} TRUE", "STEP")
    log(f"  {COL_PREVIOUSLY_NO:<35} {previously_no_true:>5,} TRUE", "STEP")
    log(f"  Both flags TRUE:                   {both_true:>5,}", "STEP")
    log("=" * 60, "STEP")
