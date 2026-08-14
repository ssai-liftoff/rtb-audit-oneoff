"""
Whitelist Audit — Part 3: Build Final Audit

Joins publisher app spend (p1) with whitelist exemptions (p2) and expands
each exemption into a flat row. One row = one publisher app × one whitelisted
domain or bundle.

Output schema:
  account_id        publisher account ID
  account_name      publisher account name
  app_id            publisher app ID
  app_name          publisher app name
  total_7d_spend    publisher app spend over last 7 days ($)
  daily_spend       average daily spend ($)
  adv_cat_id        advertiser category the exemption applies to
  exempt_domain     whitelisted advertiser domain (may be blank)
  exempt_bundle     whitelisted advertiser app bundle (may be blank)

Apps with zero exemptions are included as a single row with blank exempt_*
fields so you can see the full universe in a pivot.

Input:  output/whitelist_audit/p1_supply_apps.csv
        output/whitelist_audit/p2_app_whitelists.csv
Output: output/whitelist_audit/p3_whitelist_audit.csv
"""

import json
import os
import pandas as pd
from datetime import datetime

OUTPUT_DIR = "output/whitelist_audit"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def parse_exemptions(raw):
    try:
        return json.loads(raw or "[]") or []
    except Exception:
        return []


def expand_exemptions(row):
    """Return a list of flat dicts — one per exemption entry.
    If no exemptions, returns one row with blank exempt fields (keeps the app visible)."""
    base = {
        "account_id":     row["account_id"],
        "account_name":   row["account_name"],
        "app_id":         row["app_id"],
        "app_name":       row["app_name"],
        "total_7d_spend": row["total_7d_spend"],
        "daily_spend":    row["daily_spend"],
    }
    exemptions = parse_exemptions(row.get("exemptions", "[]"))

    if not exemptions:
        return [{**base, "adv_cat_id": None, "exempt_domain": None, "exempt_bundle": None}]

    rows = []
    for ex in exemptions:
        rows.append({
            **base,
            "adv_cat_id":    ex.get("advCatId"),
            "exempt_domain": ex.get("domain") or None,
            "exempt_bundle": ex.get("bundle") or None,
        })
    return rows


if __name__ == "__main__":
    log("═" * 60, "STEP")
    log("WHITELIST AUDIT — PART 3: BUILD AUDIT", "STEP")
    log("═" * 60, "STEP")

    p1_path = f"{OUTPUT_DIR}/p1_supply_apps.csv"
    p2_path = f"{OUTPUT_DIR}/p2_app_whitelists.csv"

    for p in [p1_path, p2_path]:
        if not os.path.exists(p):
            raise FileNotFoundError(f"Missing: {p} — run prior steps first")

    # ── Load ─────────────────────────────────────────────────────────────────
    p1 = pd.read_csv(p1_path)
    p2 = pd.read_csv(p2_path)

    p1["app_id"] = p1["app_id"].astype(str).str.strip()
    p2["app_id"] = p2["app_id"].astype(str).str.strip()

    log(f"P1 supply apps:         {len(p1):,} rows, {p1['app_id'].nunique():,} unique apps")
    log(f"P2 whitelist fetch:     {len(p2):,} rows")

    # ── Join ─────────────────────────────────────────────────────────────────
    merged = p1.merge(
        p2[["app_id", "exemptions", "exempt_count", "error"]],
        on="app_id",
        how="left",
    )
    log(f"After join:             {len(merged):,} rows")

    fetch_errors = merged["error"].notna().sum()
    if fetch_errors:
        log(f"  {fetch_errors:,} rows had API fetch errors (exemptions treated as empty)", "WARN")

    # ── Expand exemptions ─────────────────────────────────────────────────────
    log("Expanding exemptions...")
    expanded_rows = []
    for _, row in merged.iterrows():
        expanded_rows.extend(expand_exemptions(row))

    out = pd.DataFrame(expanded_rows)

    # Deduplicate — the API sometimes returns duplicate exemption entries
    before = len(out)
    out = out.drop_duplicates(
        subset=["app_id", "adv_cat_id", "exempt_domain", "exempt_bundle"]
    ).reset_index(drop=True)
    dupes = before - len(out)
    if dupes:
        log(f"Removed {dupes:,} duplicate exemption rows (API returns duplicates)", "WARN")

    # ── Stats ─────────────────────────────────────────────────────────────────
    apps_with_exemptions = merged[merged["exempt_count"].fillna(0) > 0]["app_id"].nunique()
    apps_no_exemptions   = merged[merged["exempt_count"].fillna(0) == 0]["app_id"].nunique()
    exempt_rows          = out[out["exempt_domain"].notna() | out["exempt_bundle"].notna()]

    log("═" * 60, "STEP")
    log(f"Publisher apps total:          {merged['app_id'].nunique():,}")
    log(f"  With whitelist exemptions:   {apps_with_exemptions:,}")
    log(f"  No exemptions:               {apps_no_exemptions:,}")
    log(f"Total exemption rows:          {len(exempt_rows):,}")
    log(f"  Domain exemptions:           {exempt_rows['exempt_domain'].notna().sum():,}")
    log(f"  Bundle exemptions:           {exempt_rows['exempt_bundle'].notna().sum():,}")

    if len(exempt_rows) > 0:
        top_domains = (
            exempt_rows["exempt_domain"]
            .dropna()
            .value_counts()
            .head(10)
        )
        if len(top_domains) > 0:
            log("Top 10 whitelisted domains (by publisher count):", "STEP")
            for domain, count in top_domains.items():
                log(f"  {domain:<40} {count:>4} apps")

        top_bundles = (
            exempt_rows["exempt_bundle"]
            .dropna()
            .value_counts()
            .head(10)
        )
        if len(top_bundles) > 0:
            log("Top 10 whitelisted bundles (by publisher count):", "STEP")
            for bundle, count in top_bundles.items():
                log(f"  {bundle:<40} {count:>4} apps")

    # ── Save ──────────────────────────────────────────────────────────────────
    out_path = f"{OUTPUT_DIR}/p3_whitelist_audit.csv"
    out.to_csv(out_path, index=False)
    log(f"Saved → {out_path}  ({len(out):,} rows)")
    log("═" * 60, "STEP")
