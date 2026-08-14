"""
DraftKings Missing Apps Audit

Publishers often add DK blocklist exemptions manually on only a few top apps.
This finds sibling apps (same publisher account, ≥$500/day) that do NOT yet
have any DraftKings exemption, where at least one app on the account already does.

Uplift (basic):
  dk_network_share   = draftkings_reference_daily_spend / total_network_daily_spend
  est_uplift_daily   = pub_daily_spend × dk_network_share

Requires cached exemptions from audit_draftkings_exemption_gaps.py:
  output/draftkings_exemption_audit/p2_app_exemptions.csv

Output:
  output/draftkings_exemption_audit/audit_draftkings_missing_apps.csv
"""

import json
import os
import sys

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DK_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "draftkings_exemption_audit")
SUPPLY_CACHE = os.path.join(DK_OUTPUT_DIR, "p1_apps.csv")
FALLBACK_SUPPLY = os.path.join(SCRIPT_DIR, "output", "pub_blocked_categories", "p1_apps.csv")
EXEMPTIONS_CACHE = os.path.join(DK_OUTPUT_DIR, "p2_app_exemptions.csv")
COMBOS_CSV = os.path.join(SCRIPT_DIR, "draftkings_exemption_audit", "draftkings_combos.csv")
NETWORK_TOTAL_CACHE = os.path.join(SCRIPT_DIR, "output", "pub_blocked_domains", "p3_network_total.csv")
OUTPUT_FILE = os.path.join(DK_OUTPUT_DIR, "audit_draftkings_missing_apps.csv")

GAMBLING_CAT = "V1-6"
DK_DOMAIN_HINT = "draftkings"
SPEND_THRESHOLD = 500


def log(msg, level="INFO"):
    from datetime import datetime

    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def norm_domain(val):
    return str(val or "").strip().lower()


def parse_exemptions(val):
    if isinstance(val, list):
        return val
    if pd.isna(val) or val == "":
        return []
    try:
        parsed = json.loads(val)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    return []


def load_combo_domains():
    if os.path.exists(COMBOS_CSV):
        combos = pd.read_csv(COMBOS_CSV, dtype=str)
        return {norm_domain(d) for d in combos["domain"] if norm_domain(d)}
    return {
        norm_domain(d)
        for d in [
            "draftkings.com",
            "casino.draftkings.com",
            "sportsbook.draftkings.com",
        ]
    }


def is_draftkings_exemption(exemption, combo_domains):
    if str(exemption.get("advCatId", "")).strip().upper() != GAMBLING_CAT:
        return False
    domain = norm_domain(exemption.get("domain"))
    if not domain:
        return False
    return DK_DOMAIN_HINT in domain or domain in combo_domains


def has_draftkings_exemption(exemptions, combo_domains):
    return any(is_draftkings_exemption(ex, combo_domains) for ex in exemptions)


def load_supply():
    for path in [SUPPLY_CACHE, FALLBACK_SUPPLY]:
        if os.path.exists(path):
            df = pd.read_csv(path, dtype=str)
            df["daily_spend"] = pd.to_numeric(df["daily_spend"], errors="coerce").fillna(0)
            df = df[df["daily_spend"] >= SPEND_THRESHOLD].copy()
            for col in ["account_id", "app_id", "account_name", "app_name", "pub_market_id", "region", "am_name"]:
                if col in df.columns:
                    df[col] = df[col].fillna("").astype(str).str.strip()
            log(f"Supply: {len(df):,} apps ≥ ${SPEND_THRESHOLD:,}/day")
            return df
    raise FileNotFoundError(
        "Missing supply cache. Run audit_draftkings_exemption_gaps.py first "
        "or ensure output/pub_blocked_categories/p1_apps.csv exists."
    )


def load_exemptions():
    if not os.path.exists(EXEMPTIONS_CACHE):
        raise FileNotFoundError(
            f"Missing {EXEMPTIONS_CACHE}. Run audit_draftkings_exemption_gaps.py first."
        )
    df = pd.read_csv(EXEMPTIONS_CACHE, dtype=str)
    df["exemptions"] = df["exemptions"].apply(parse_exemptions)
    log(f"Exemptions: {len(df):,} apps from cache")
    return df


def load_spend_denominators():
    if not os.path.exists(COMBOS_CSV):
        raise FileNotFoundError(f"Missing {COMBOS_CSV}. Run --fetch-combos on DK audit first.")
    if not os.path.exists(NETWORK_TOTAL_CACHE):
        raise FileNotFoundError(
            f"Missing {NETWORK_TOTAL_CACHE}. Run audit_pub_blocked_domains.py step 3 first."
        )
    combos = pd.read_csv(COMBOS_CSV)
    combos["network_daily_spend"] = pd.to_numeric(combos["network_daily_spend"], errors="coerce").fillna(0)
    dk_daily = float(combos["network_daily_spend"].sum())
    network_daily = float(pd.read_csv(NETWORK_TOTAL_CACHE)["total_daily_spend"].iloc[0])
    share = dk_daily / network_daily if network_daily > 0 else 0
    log(f"DK reference daily spend: ${dk_daily:,.2f}")
    log(f"Total network daily spend: ${network_daily:,.2f}")
    log(f"DK network share: {share:.6f}")
    return dk_daily, network_daily, share


def build_missing_apps_audit(apps_df, exemptions_df, combo_domains, dk_daily, network_daily, dk_share):
    log("Building missing sibling apps audit...", "STEP")

    exempt_by_app = {}
    for _, row in exemptions_df.iterrows():
        app_id = str(row["app_id"]).strip()
        exempt_by_app[app_id] = row.get("exemptions") or []

    apps = apps_df.copy()
    apps["app_id"] = apps["app_id"].astype(str).str.strip()
    apps["account_id"] = apps["account_id"].astype(str).str.strip()
    apps["has_dk_exemption"] = apps["app_id"].apply(
        lambda aid: has_draftkings_exemption(exempt_by_app.get(aid, []), combo_domains)
    )

    account_stats = (
        apps.groupby("account_id", as_index=False)
        .agg(
            account_name=("account_name", "first"),
            region=("region", "first"),
            am_name=("am_name", "first"),
            qualifying_app_count=("app_id", "count"),
            exempted_app_count=("has_dk_exemption", "sum"),
        )
    )
    accounts_with_exemptions = set(
        account_stats.loc[account_stats["exempted_app_count"] > 0, "account_id"]
    )
    log(f"  Accounts with ≥1 DK-exempted app: {len(accounts_with_exemptions):,}")

    missing = apps[
        apps["account_id"].isin(accounts_with_exemptions) & ~apps["has_dk_exemption"]
    ].copy()
    log(f"  High-spend sibling apps without DK exemption: {len(missing):,}")

    exempted_apps_by_account = (
        apps[apps["has_dk_exemption"]]
        .groupby("account_id")["app_name"]
        .apply(lambda s: "; ".join(sorted(set(s.astype(str)))[:5]))
        .to_dict()
    )
    stats_map = account_stats.set_index("account_id")

    rows = []
    for _, row in missing.iterrows():
        account_id = row["account_id"]
        pub_daily = float(row["daily_spend"])
        acct = stats_map.loc[account_id]
        rows.append(
            {
                "pub_account": row["account_name"],
                "pub_account_id": account_id,
                "region": row.get("region", acct.get("region", "")),
                "am_name": row.get("am_name", acct.get("am_name", "")),
                "pub_app": row["app_name"],
                "pub_app_id": row["app_id"],
                "pub_market_id": row.get("pub_market_id", ""),
                "pub_daily_spend": round(pub_daily, 4),
                "account_exempted_app_count": int(acct["exempted_app_count"]),
                "account_qualifying_app_count": int(acct["qualifying_app_count"]),
                "account_exempted_apps_sample": exempted_apps_by_account.get(account_id, ""),
                "dk_network_daily_total": round(dk_daily, 4),
                "network_daily_total": round(network_daily, 4),
                "dk_network_share": round(dk_share, 8),
                "est_uplift_daily": round(pub_daily * dk_share, 4),
            }
        )

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("est_uplift_daily", ascending=False).reset_index(drop=True)
    out.to_csv(OUTPUT_FILE, index=False)
    log(f"Output → {OUTPUT_FILE} ({len(out):,} rows)")
    return out


def main():
    combo_domains = load_combo_domains()
    apps_df = load_supply()
    exemptions_df = load_exemptions()
    dk_daily, network_daily, dk_share = load_spend_denominators()
    output = build_missing_apps_audit(
        apps_df, exemptions_df, combo_domains, dk_daily, network_daily, dk_share
    )

    log("═" * 60, "STEP")
    log("MISSING APPS AUDIT COMPLETE", "STEP")
    if output.empty:
        log("No missing sibling apps found", "WARN")
    else:
        log(f"Rows: {len(output):,}")
        log(f"Unique accounts: {output['pub_account_id'].nunique():,}")
        print("\nTop 10 by est_uplift_daily:")
        print(
            output[
                [
                    "pub_account", "pub_app", "account_exempted_app_count",
                    "pub_daily_spend", "est_uplift_daily",
                ]
            ]
            .head(10)
            .to_string(index=False)
        )
    log("═" * 60, "STEP")


if __name__ == "__main__":
    main()
