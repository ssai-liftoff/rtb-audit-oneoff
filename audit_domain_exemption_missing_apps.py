"""
Domain Exemption Missing Apps Audit

Finds high-spend sibling apps (≥$500/day) missing domain exemptions that
other apps on the same account already have configured.

Only missing domains with ≥$500/day network spend (from route cache) are
included in the gap list and uplift. Domains are ordered by network spend
(highest opportunity first).

  missing_exempted_domains        = (account domains − app domains), filtered ≥$500/day network
  missing_domains_network_share   = sum(network spend for those domains) / total network
  est_uplift_daily                = pub_daily_spend × missing_domains_network_share

Requires cached data from audit_domain_exemption_gaps.py:
  output/domain_exemption_audit/p2_app_exemptions.csv
  output/domain_exemption_audit/p3_network_routes.csv

Output:
  output/domain_exemption_audit/audit_domain_exemption_missing_apps.csv
"""

import json
import os

import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "domain_exemption_audit")
SUPPLY_CACHE = os.path.join(OUTPUT_DIR, "p1_apps.csv")
FALLBACK_SUPPLY = os.path.join(SCRIPT_DIR, "output", "pub_blocked_categories", "p1_apps.csv")
EXEMPTIONS_CACHE = os.path.join(OUTPUT_DIR, "p2_app_exemptions.csv")
ROUTES_CACHE = os.path.join(OUTPUT_DIR, "p3_network_routes.csv")
NETWORK_TOTAL_CACHE = os.path.join(SCRIPT_DIR, "output", "pub_blocked_domains", "p3_network_total.csv")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "audit_domain_exemption_missing_apps.csv")

SPEND_THRESHOLD = 500
DOMAIN_MIN_DAILY = 500


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


def exemption_domains(exemptions):
    return {norm_domain(ex.get("domain")) for ex in exemptions if norm_domain(ex.get("domain"))}


def domain_network_daily(domain, domain_lookup):
    return float(domain_lookup.get(norm_domain(domain), 0) or 0)


def filter_high_spend_domains(domains, domain_lookup, min_daily=DOMAIN_MIN_DAILY):
    return {d for d in domains if domain_network_daily(d, domain_lookup) >= min_daily}


def domains_sorted_by_spend(domains, domain_lookup):
    return sorted(domains, key=lambda d: domain_network_daily(d, domain_lookup), reverse=True)


def domains_network_daily(domains, domain_lookup):
    return sum(domain_network_daily(d, domain_lookup) for d in domains)


def format_domain_list(domains, domain_lookup):
    ordered = domains_sorted_by_spend(domains, domain_lookup)
    return ",".join(ordered)


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
        "Missing supply cache. Run audit_domain_exemption_gaps.py first "
        "or ensure output/pub_blocked_categories/p1_apps.csv exists."
    )


def load_exemptions():
    if not os.path.exists(EXEMPTIONS_CACHE):
        raise FileNotFoundError(
            f"Missing {EXEMPTIONS_CACHE}. Run audit_domain_exemption_gaps.py first."
        )
    df = pd.read_csv(EXEMPTIONS_CACHE, dtype=str)
    df["exemptions"] = df["exemptions"].apply(parse_exemptions)
    log(f"Exemptions: {len(df):,} apps from cache")
    return df


def load_domain_network_lookup():
    if not os.path.exists(ROUTES_CACHE):
        raise FileNotFoundError(
            f"Missing {ROUTES_CACHE}. Run audit_domain_exemption_gaps.py first."
        )
    if not os.path.exists(NETWORK_TOTAL_CACHE):
        raise FileNotFoundError(
            f"Missing {NETWORK_TOTAL_CACHE}. Run audit_pub_blocked_domains.py step 3 first."
        )
    routes = pd.read_csv(ROUTES_CACHE)
    routes["network_daily_spend"] = pd.to_numeric(routes["network_daily_spend"], errors="coerce").fillna(0)
    routes["domain"] = routes["domain"].fillna("").astype(str).str.strip().str.lower()
    domain_lookup = routes.groupby("domain")["network_daily_spend"].sum().to_dict()
    high_spend_domains = sum(1 for v in domain_lookup.values() if v >= DOMAIN_MIN_DAILY)
    network_daily = float(pd.read_csv(NETWORK_TOTAL_CACHE)["total_daily_spend"].iloc[0])
    log(f"Network spend mapped for {len(domain_lookup):,} domains ({high_spend_domains:,} ≥ ${DOMAIN_MIN_DAILY:,}/day)")
    log(f"Total network daily spend: ${network_daily:,.2f}")
    return domain_lookup, network_daily


def format_exempted_apps_list(acct_apps_df):
    ordered = acct_apps_df.sort_values("daily_spend", ascending=False)
    parts = []
    for _, app_row in ordered.iterrows():
        name = str(app_row["app_name"]).strip()
        spend = float(app_row["daily_spend"])
        parts.append(f"{name} (${spend:,.0f}/day)")
    return "; ".join(parts)


def build_missing_apps_audit(apps_df, exemptions_df, domain_lookup, network_daily):
    log("Building missing sibling apps audit...", "STEP")

    exempt_by_app = {}
    for _, row in exemptions_df.iterrows():
        app_id = str(row["app_id"]).strip()
        exempt_by_app[app_id] = row.get("exemptions") or []

    apps = apps_df.copy()
    apps["app_id"] = apps["app_id"].astype(str).str.strip()
    apps["account_id"] = apps["account_id"].astype(str).str.strip()
    apps["app_exempted_domains"] = apps["app_id"].apply(
        lambda aid: exemption_domains(exempt_by_app.get(aid, []))
    )
    apps["has_domain_exemption"] = apps["app_exempted_domains"].apply(bool)

    account_stats = (
        apps.groupby("account_id", as_index=False)
        .agg(
            account_name=("account_name", "first"),
            region=("region", "first"),
            am_name=("am_name", "first"),
            exempted_app_count=("has_domain_exemption", "sum"),
        )
    )
    accounts_with_exemptions = set(
        account_stats.loc[account_stats["exempted_app_count"] > 0, "account_id"]
    )
    log(f"  Accounts with ≥1 domain-exempted app: {len(accounts_with_exemptions):,}")

    account_domains = {}
    account_exempted_apps_text = {}
    for account_id in accounts_with_exemptions:
        acct_apps = apps[(apps["account_id"] == account_id) & apps["has_domain_exemption"]]
        domains = set()
        for app_id in acct_apps["app_id"]:
            domains.update(exemption_domains(exempt_by_app.get(app_id, [])))
        account_domains[account_id] = domains
        account_exempted_apps_text[account_id] = format_exempted_apps_list(acct_apps)

    candidate = apps[apps["account_id"].isin(accounts_with_exemptions)].copy()
    candidate["missing_domains_raw"] = candidate.apply(
        lambda row: account_domains.get(row["account_id"], set()) - row["app_exempted_domains"],
        axis=1,
    )
    candidate["missing_exempted_domains"] = candidate["missing_domains_raw"].apply(
        lambda domains: filter_high_spend_domains(domains, domain_lookup)
    )
    missing = candidate[candidate["missing_exempted_domains"].apply(bool)].copy()
    log(
        f"  Apps missing ≥1 high-spend domain exemption (≥${DOMAIN_MIN_DAILY:,}/day network): "
        f"{len(missing):,}"
    )

    stats_map = account_stats.set_index("account_id")

    rows = []
    for _, row in missing.iterrows():
        account_id = row["account_id"]
        pub_daily = float(row["daily_spend"])
        acct = stats_map.loc[account_id]
        account_domains_set = account_domains.get(account_id, set())
        missing_domains = row["missing_exempted_domains"]
        missing_network = domains_network_daily(missing_domains, domain_lookup)
        share = missing_network / network_daily if network_daily > 0 else 0
        app_domains = row["app_exempted_domains"]

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
                "apps_with_exemptions_count": int(acct["exempted_app_count"]),
                "account_exempted_apps": account_exempted_apps_text.get(account_id, ""),
                "account_exempted_domains": format_domain_list(
                    filter_high_spend_domains(account_domains_set, domain_lookup),
                    domain_lookup,
                ),
                "app_current_exempted_domains": format_domain_list(app_domains, domain_lookup),
                "missing_exempted_domains": format_domain_list(missing_domains, domain_lookup),
                "missing_exempted_domain_count": len(missing_domains),
                "missing_domains_network_daily": round(missing_network, 4),
                "network_daily_total": round(network_daily, 4),
                "missing_domains_network_share": round(share, 8),
                "est_uplift_daily": round(pub_daily * share, 4),
            }
        )

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("est_uplift_daily", ascending=False).reset_index(drop=True)
    out.to_csv(OUTPUT_FILE, index=False)
    log(f"Output → {OUTPUT_FILE} ({len(out):,} rows)")
    return out


def main():
    apps_df = load_supply()
    exemptions_df = load_exemptions()
    domain_lookup, network_daily = load_domain_network_lookup()
    output = build_missing_apps_audit(apps_df, exemptions_df, domain_lookup, network_daily)

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
                    "pub_account",
                    "pub_app",
                    "missing_exempted_domains",
                    "missing_domains_network_share",
                    "est_uplift_daily",
                ]
            ]
            .head(10)
            .to_string(index=False)
        )
    log("═" * 60, "STEP")


if __name__ == "__main__":
    main()
