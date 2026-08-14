"""
Fraud Banking Audit — Part 4: Build Final Output

Joins the match results from Part 2 with the L60D spend data from Part 3
(Sales Ops accounts only) and deduplicates to one row per potential new
fraud publisher.

Output columns (review-facing names):
  potential fraud pub id
  account name
  no of matched fraud pubs
  matched fraud pub ids
  matched fraud pub names
  matched fraud entity?          — match type of the highest-confidence match
  matched fraud entity value     — the actual matched value
  Account date created
  l60d spend / l60d pub revenue / l60d unr
  am name / account email / is deleted

Part 5 appends:
  Is matched pubs dormant?
  Is previously marked as "No"?

Inputs:
  - output/fraud_banking_audit/p2_matches.csv
  - output/fraud_banking_audit/p3_spend_data.csv  (Sales Ops accounts only)

Outputs:
  - output/fraud_banking_audit/p4_fraud_banking_audit.csv
"""

import os
import pandas as pd
from datetime import datetime

OUTPUT_DIR = "output/fraud_banking_audit"
MATCHES_CSV = f"{OUTPUT_DIR}/p2_matches.csv"
SPEND_CSV   = f"{OUTPUT_DIR}/p3_spend_data.csv"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Match type priority — lower number = higher confidence.
# The representative row shown per new fraud pub uses the highest-confidence match.
MATCH_PRIORITY = {
    "account_number": 1,
    "iban":           2,
    "paypal_email":   3,
    "email":          4,
    "address":        5,
    "identity":       6,
    "email_domain":   7,
    "typo_email":     8,
}

# Final review-facing column names and order (p5 appends two flag columns after these).
OUTPUT_COLUMNS = [
    ("new_fraud_pub_id",        "potential fraud pub id"),
    ("account_name",            "account name"),
    ("no_of_matched_fraud_pubs","no of matched fraud pubs"),
    ("matched_fraud_pub_ids",   "matched fraud pub ids"),
    ("matched_fraud_pub_names", "matched fraud pub names"),
    ("matched_on",              "matched fraud entity?"),
    ("matched_fraud_entity",    "matched fraud entity value"),
    ("date_created",            "Account date created"),
    ("l60d_spend",              "l60d spend"),
    ("l60d_pub_revenue",        "l60d pub revenue"),
    ("l60d_unr",                "l60d unr"),
    ("am_name",                 "am name"),
    ("account_email",           "account email"),
    ("is_deleted",              "is deleted"),
]


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def row_priority(matched_on_str):
    """Return the lowest priority number (= highest confidence) for a match row."""
    types = [t.strip() for t in str(matched_on_str).split(",")]
    return min((MATCH_PRIORITY.get(t, 99) for t in types), default=99)


def deduplicate_matches(matches_df):
    """
    Collapse all (new_fraud_pub_id, ivt_pub_id) pairs into one row per
    new_fraud_pub_id. The representative matched_on / matched_fraud_entity
    comes from the highest-confidence match for that publisher.
    """
    log("Deduplicating matches to 1 row per potential fraud publisher...", "STEP")

    matches_df = matches_df.copy()
    matches_df["_priority"] = matches_df["matched_on"].apply(row_priority)

    rows = []
    for pub_id, group in matches_df.groupby("new_fraud_pub_id", sort=False):
        group_sorted = group.sort_values("_priority")
        best = group_sorted.iloc[0]

        # All unique IVT IDs this pub matched (exclude "N/A" typo-only placeholders)
        all_ivt_ids = [
            str(x) for x in group["matched_fraud_pub_id"].unique()
            if str(x).strip() not in ("", "N/A")
        ]

        # All unique IVT names (preserving order, paired with their IDs)
        seen_ids = set()
        all_ivt_names = []
        for _, r in group.iterrows():
            ivt_id = str(r["matched_fraud_pub_id"]).strip()
            if ivt_id not in ("", "N/A") and ivt_id not in seen_ids:
                seen_ids.add(ivt_id)
                all_ivt_names.append(str(r["matched_fraud_pub_name"]))

        rows.append({
            "new_fraud_pub_id":        pub_id,
            "no_of_matched_fraud_pubs": len(all_ivt_ids),
            "matched_fraud_pub_ids":   ", ".join(all_ivt_ids) if all_ivt_ids else "N/A",
            "matched_fraud_pub_names": ", ".join(all_ivt_names) if all_ivt_names else "N/A",
            "matched_on":              best["matched_on"],
            "matched_fraud_entity":    best["matched_fraud_entity"],
        })

    deduped = pd.DataFrame(rows)
    log(f"  {len(matches_df):,} match rows → {len(deduped):,} unique potential fraud publishers")
    return deduped


if __name__ == "__main__":
    log("=" * 60, "STEP")
    log("FRAUD BANKING AUDIT — PART 4: BUILD FINAL OUTPUT", "STEP")
    log("=" * 60, "STEP")

    if not os.path.exists(MATCHES_CSV):
        raise FileNotFoundError(f"Matches not found at {MATCHES_CSV}. Run p2 first.")

    matches_df = pd.read_csv(MATCHES_CSV, dtype=str).fillna("")
    log(f"Loaded {len(matches_df):,} match rows from Part 2")

    # Load spend data — already filtered to Sales Ops by p3
    if os.path.exists(SPEND_CSV):
        spend_df = pd.read_csv(SPEND_CSV, dtype={"pub_id": str}).fillna("")
        log(f"Loaded {len(spend_df):,} spend rows from Part 3 (Sales Ops only)")
    else:
        log("No spend data found — proceeding without it (run p3 to add spend columns)", "WARN")
        spend_df = pd.DataFrame(columns=[
            "pub_id", "account_name", "is_deleted", "account_email",
            "date_created", "am_name", "l60d_spend", "l60d_pub_revenue", "l60d_unr"
        ])

    # ── Step 1: Deduplicate matches to 1 row per new fraud pub ────────────────
    deduped_df = deduplicate_matches(matches_df)

    # ── Step 2: Left join spend data — keep all matched publishers ───────────
    # Left join preserves all 2k+ matched publishers; spend/metadata columns
    # will be blank for any publisher with no Looker data.
    if not spend_df.empty:
        final_df = deduped_df.merge(
            spend_df.rename(columns={"pub_id": "new_fraud_pub_id"}),
            on="new_fraud_pub_id",
            how="left"
        )
    else:
        final_df = deduped_df.copy()
        for col in ["account_name", "is_deleted", "account_email", "date_created",
                    "am_name", "l60d_spend", "l60d_pub_revenue", "l60d_unr"]:
            final_df[col] = ""

    # ── Step 3: Filter to Sales Ops (keep blanks — can't confirm they're not) ─
    # am_name == "Sales Ops"  →  confirmed Sales Ops, include
    # am_name blank / NaN     →  no Looker data, include (can't exclude them)
    # am_name == anything else →  different team, exclude
    before = len(final_df)
    final_df["am_name"] = final_df["am_name"].fillna("")
    final_df = final_df[
        (final_df["am_name"].str.strip() == "Sales Ops") |
        (final_df["am_name"].str.strip() == "")
    ].copy()
    log(f"Sales Ops filter: {before:,} → {len(final_df):,} publishers "
        f"({(final_df['am_name'].str.strip() == 'Sales Ops').sum()} confirmed Sales Ops, "
        f"{(final_df['am_name'].str.strip() == '').sum()} no AM data)")

    # ── Step 3: Column order and review-facing names ─────────────────────────
    internal_cols = [internal for internal, _ in OUTPUT_COLUMNS]
    for col in internal_cols:
        if col not in final_df.columns:
            final_df[col] = ""
    final_df = final_df[internal_cols].copy()
    final_df = final_df.rename(columns=dict(OUTPUT_COLUMNS))

    # Sort: highest spend first
    final_df["_spend_sort"] = pd.to_numeric(final_df["l60d spend"], errors="coerce").fillna(0)
    final_df = final_df.sort_values("_spend_sort", ascending=False).drop(columns=["_spend_sort"])

    out_path = f"{OUTPUT_DIR}/p4_fraud_banking_audit.csv"
    final_df.to_csv(out_path, index=False)
    log(f"Final audit saved → {out_path}")

    # ── Summary ───────────────────────────────────────────────────────────────
    log("=" * 60, "STEP")
    log("PART 4 COMPLETE — SUMMARY", "STEP")
    confirmed_so = (final_df["am name"].str.strip() == "Sales Ops").sum()
    no_am = (final_df["am name"].str.strip() == "").sum()
    log(f"  Unique potential fraud publishers: {len(final_df):>6,}", "STEP")
    log(f"    ↳ Confirmed Sales Ops:           {confirmed_so:>6,}", "STEP")
    log(f"    ↳ No AM data (included):         {no_am:>6,}", "STEP")

    from collections import Counter
    type_counts = Counter()
    for val in final_df["matched fraud entity?"]:
        for mt in str(val).split(","):
            mt = mt.strip()
            if mt:
                type_counts[mt] += 1
    log("  Match type breakdown (representative match per pub):", "STEP")
    for mt, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        log(f"    {mt:<22} {cnt:>5}", "STEP")

    total_spend = pd.to_numeric(final_df["l60d spend"], errors="coerce").sum()
    log(f"  Total L60D spend at risk:          ${total_spend:>10,.2f}", "STEP")
    log(f"  Output: {out_path}", "STEP")
    log("=" * 60, "STEP")
