"""
EMEA V2-11 Society Block Audit — Part 2: Build Final Audit Output

Filters accounts and apps where V2-11 (Society) is in their adCatBlocklist.
For app-level blocks, surfaces any blocklistExemptions scoped to V2-11.
Also adds a column indicating whether V2-16 (Dating) is blocked at the same level.

Output columns:
  Level, Account ID, Account Name, App ID, App Name, Spend,
  V2-11 Blocked At, V2-16 (Dating) Blocked, Exempted Domains/Bundles, Exemption Details

Sorted by Spend descending.

Inputs:  emea_v211_audit/input_publishers.csv
         output/emea_v211_audit/p1_account_blocklists.csv
         output/emea_v211_audit/p1_app_blocklists.csv
Output:  output/emea_v211_audit/p2_v211_audit.csv
"""

import os
import json
import pandas as pd
from datetime import datetime

INPUT_CSV  = "emea_v211_audit/input_publishers.csv"
OUTPUT_DIR = "output/emea_v211_audit"
TARGET_CAT  = "V2-11"
DATING_CAT  = "V2-16"

os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def parse_list(val):
    try:
        return json.loads(val or "[]")
    except Exception:
        return []


def parse_spend(val):
    """Parse Looker-formatted spend like '$207,876' to float."""
    if pd.isna(val):
        return 0.0
    return float(str(val).replace("$", "").replace(",", "").strip() or 0)


def get_v211_exemptions(exemptions):
    """Return exemptions scoped to V2-11 (advCatId = 'V2-11')."""
    return [e for e in exemptions if e.get("advCatId") == TARGET_CAT]


if __name__ == "__main__":
    log("═" * 60, "STEP")
    log("EMEA V2-11 AUDIT — PART 2: BUILD AUDIT", "STEP")
    log("═" * 60, "STEP")

    for path in [
        INPUT_CSV,
        f"{OUTPUT_DIR}/p1_account_blocklists.csv",
        f"{OUTPUT_DIR}/p1_app_blocklists.csv",
    ]:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing: {path}")

    # Load inputs
    publishers = pd.read_csv(INPUT_CSV)
    publishers.columns = ["account_id", "account_name", "app_id", "app_name", "spend_raw"]
    publishers["account_id"] = publishers["account_id"].astype(str).str.strip()
    publishers["app_id"]     = publishers["app_id"].astype(str).str.strip()
    publishers["spend"]      = publishers["spend_raw"].apply(parse_spend)

    acct_bl = pd.read_csv(f"{OUTPUT_DIR}/p1_account_blocklists.csv")
    acct_bl["account_id"] = acct_bl["account_id"].astype(str).str.strip()

    app_bl = pd.read_csv(f"{OUTPUT_DIR}/p1_app_blocklists.csv")
    app_bl["app_id"] = app_bl["app_id"].astype(str).str.strip()

    # Build lookup dicts
    acct_cats = {
        r["account_id"]: parse_list(r.get("cat_blocklist"))
        for _, r in acct_bl.iterrows()
    }
    app_data = {
        r["app_id"]: {
            "cat_bl":     parse_list(r.get("cat_blocklist")),
            "exemptions": parse_list(r.get("exemptions")),
        }
        for _, r in app_bl.iterrows()
    }

    # app_id → app_name lookup (for building account-level exemption summaries)
    app_id_to_name = dict(zip(publishers["app_id"], publishers["app_name"]))

    def account_exemption_summary(acct_id):
        """
        For an account-level block, check all apps under this account for V2-11
        exemptions. Returns a dict with aggregated exemption info.
        """
        account_apps = publishers[publishers["account_id"] == acct_id][["app_id", "app_name"]].drop_duplicates()
        apps_with_ex = []
        all_domains  = set()
        detail_parts = []

        for _, app_row in account_apps.iterrows():
            bl = app_data.get(app_row["app_id"])
            if bl is None:
                continue
            exemptions = get_v211_exemptions(bl["exemptions"])
            if not exemptions:
                continue

            app_name = app_row["app_name"]
            apps_with_ex.append(app_name)

            domains = sorted(set(
                ex.get("domain") or ex.get("bundle") or ""
                for ex in exemptions
                if ex.get("domain") or ex.get("bundle")
            ))
            all_domains.update(domains)
            if domains:
                detail_parts.append(f"{app_name} [{', '.join(domains)}]")

        return {
            "has_exemption":        len(apps_with_ex) > 0,
            "num_apps_with_ex":     len(apps_with_ex),
            "apps_with_ex":         ", ".join(apps_with_ex),
            "exempted_domains":     ", ".join(sorted(all_domains)),
            "exemption_details":    " | ".join(detail_parts),
        }

    results = []

    # ── Account-level blocks ──────────────────────────────────────────────
    log("Checking account-level V2-11 blocks...")
    seen_accounts = set()
    for _, row in publishers.iterrows():
        acct_id = row["account_id"]
        if acct_id in seen_accounts:
            continue
        seen_accounts.add(acct_id)

        cats = acct_cats.get(acct_id, [])
        if TARGET_CAT not in cats:
            continue

        acct_spend = publishers[publishers["account_id"] == acct_id]["spend"].sum()
        ex = account_exemption_summary(acct_id)

        results.append({
            "Level":                    "account",
            "Account ID":               acct_id,
            "Account Name":             row["account_name"],
            "App ID":                   "",
            "App Name":                 "",
            "Spend":                    round(acct_spend, 2),
            "V2-11 Blocked At":         "account",
            "V2-16 (Dating) Blocked":   DATING_CAT in cats,
            "Has Exemption":            ex["has_exemption"],
            "Num Apps with Exemption":  ex["num_apps_with_ex"] if ex["has_exemption"] else "",
            "Apps with Exemption":      ex["apps_with_ex"],
            "Exempted Domains":         ex["exempted_domains"],
            "Exemption Details":        ex["exemption_details"],
        })

    log(f"  Account-level blocks: {len(results):,}")

    # ── App-level blocks ──────────────────────────────────────────────────
    log("Checking app-level V2-11 blocks...")
    app_count_before = len(results)

    for _, row in publishers.iterrows():
        app_id = row["app_id"]
        bl = app_data.get(app_id)
        if bl is None:
            continue
        if TARGET_CAT not in bl["cat_bl"]:
            continue

        exemptions = get_v211_exemptions(bl["exemptions"])
        has_exemption = len(exemptions) > 0
        domains = sorted(set(
            ex.get("domain") or ex.get("bundle") or ""
            for ex in exemptions
            if ex.get("domain") or ex.get("bundle")
        ))

        # Check Dating block: prefer app-level, fall back to account-level
        app_dating = DATING_CAT in bl["cat_bl"]
        acct_dating = DATING_CAT in acct_cats.get(row["account_id"], [])
        dating_blocked = app_dating or acct_dating

        results.append({
            "Level":                    "app",
            "Account ID":               row["account_id"],
            "Account Name":             row["account_name"],
            "App ID":                   app_id,
            "App Name":                 row["app_name"],
            "Spend":                    round(row["spend"], 2),
            "V2-11 Blocked At":         "app",
            "V2-16 (Dating) Blocked":   dating_blocked,
            "Has Exemption":            has_exemption,
            "Num Apps with Exemption":  "",
            "Apps with Exemption":      "",
            "Exempted Domains":         ", ".join(domains),
            "Exemption Details":        " | ".join(
                f"{row['app_name']} [{d}]" for d in domains
            ) if domains else "",
        })

    log(f"  App-level blocks: {len(results) - app_count_before:,}")

    # ── Save output ───────────────────────────────────────────────────────
    if not results:
        log("No V2-11 blocks found", "WARN")
        final_df = pd.DataFrame()
    else:
        final_df = (
            pd.DataFrame(results)
            .sort_values("Spend", ascending=False)
            .reset_index(drop=True)
        )

    out_path = f"{OUTPUT_DIR}/p2_v211_audit.csv"
    final_df.to_csv(out_path, index=False)
    log(f"Saved → {out_path}")

    log("═" * 60, "STEP")
    if len(final_df):
        log(f"Total blocked entries:    {len(final_df):,}")
        log(f"  Account-level:          {(final_df['Level'] == 'account').sum():,}")
        log(f"  App-level:              {(final_df['Level'] == 'app').sum():,}")
        log(f"  Also Dating (V2-16):    {final_df['V2-16 (Dating) Blocked'].sum():,}")
        log(f"  With exemptions:        {final_df['Has Exemption'].sum():,}")
        acct_ex = final_df[(final_df["Level"] == "account") & (final_df["Has Exemption"])]["Num Apps with Exemption"]
        if len(acct_ex):
            log(f"  Apps exempted (acct):   {acct_ex.apply(lambda x: int(x) if str(x).isdigit() else 0).sum():,}")
        log(f"  Total spend affected:   ${final_df['Spend'].sum():,.0f}")
        log("")
        log("Top 5 by spend:")
        for _, r in final_df.head(5).iterrows():
            name = r["App Name"] if r["App Name"] else r["Account Name"]
            log(f"  {name:<45} ${r['Spend']:>12,.0f}  [{r['Level']}]")
    log("═" * 60, "STEP")
    log("COMPLETE", "STEP")
    log("═" * 60, "STEP")
