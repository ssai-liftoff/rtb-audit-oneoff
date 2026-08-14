"""
DraftKings Exemption vs Domain/Market Block Conflicts

For every app with at least one DraftKings V1-6 blocklist exemption, checks
whether DraftKings is still hard-blocked via adDomainBlacklist or market ID
blacklists (account or app level).

Category exemptions do not override hard domain/market blocks — this audit
surfaces that conflict.

Rules:
  - Account-level block  → flag every DK-exempted app on that account
  - App-level block      → flag only if that specific app has a DK exemption

Requires:
  output/draftkings_exemption_audit/p2_app_exemptions.csv
  output/pub_blocked_domains/p3_domain_spend.csv      (optional, for uplift)
  output/pub_blocked_domains/p3_market_spend.csv      (optional, for uplift)
  output/pub_blocked_domains/p3_network_total.csv     (optional, for uplift)

Output:
  output/draftkings_exemption_audit/audit_draftkings_exemption_block_conflicts.csv
  output/draftkings_exemption_audit/audit_draftkings_exemption_block_conflicts_by_app.csv
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "draftkings_exemption_audit")
DOMAIN_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "pub_blocked_domains")

EXEMPTIONS_CACHE = os.path.join(OUTPUT_DIR, "p2_app_exemptions.csv")
COMBOS_CSV = os.path.join(SCRIPT_DIR, "draftkings_exemption_audit", "draftkings_combos.csv")
ACCOUNT_BLOCKS_CACHE = os.path.join(OUTPUT_DIR, "p4_account_blocklists.csv")
APP_BLOCKS_CACHE = os.path.join(OUTPUT_DIR, "p4_app_blocklists.csv")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "audit_draftkings_exemption_block_conflicts.csv")
OUTPUT_BY_APP = os.path.join(OUTPUT_DIR, "audit_draftkings_exemption_block_conflicts_by_app.csv")

PUB_DOMAIN_ACCOUNT_BLOCKS = os.path.join(DOMAIN_OUTPUT_DIR, "p2_account_blocklists.csv")
PUB_DOMAIN_APP_BLOCKS = os.path.join(DOMAIN_OUTPUT_DIR, "p2_app_blocklists.csv")
DOMAIN_SPEND_CACHE = os.path.join(DOMAIN_OUTPUT_DIR, "p3_domain_spend.csv")
MARKET_SPEND_CACHE = os.path.join(DOMAIN_OUTPUT_DIR, "p3_market_spend.csv")
NETWORK_TOTAL_CACHE = os.path.join(DOMAIN_OUTPUT_DIR, "p3_network_total.csv")

SUPPLY_CACHES = [
    os.path.join(OUTPUT_DIR, "p1_apps.csv"),
    os.path.join(DOMAIN_OUTPUT_DIR, "p1_apps.csv"),
    os.path.join(SCRIPT_DIR, "output", "pub_blocked_categories", "p1_apps.csv"),
]

GAMBLING_CAT = "V1-6"
DK_DOMAIN_HINT = "draftkings"
DK_MARKET_IDS = {
    "1375031369",
    "1462060332",
    "com.draftkings.casino",
    "com.draftkings.sportsbook",
}
MAX_WORKERS = 20
API_SLEEP_SEC = 0.05

DASHBOARD_API_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2NvdW50IjoiNjgxM2JmZDUxMDRiNDAwMDExMzcyNDM2Iiw"
    "iaXNNYW5hZ2VkQWNjb3VudCI6ZmFsc2UsImFwaVRva2VuIjoiN2FhMThlNTdmMTU0NmQ2YmQ1NDJlMDc2YWI5NzI3"
    "YzMiLCJlbWFpbCI6InNzYWlAbGlmdG9mZi5pbyIsImZlYXR1cmVzIjpbImZlYXR1cmVfbWVkaWF0aW9uX2FjY2Vzc"
    "yJdLCJpZCI6IjY4MTNiZmQ1MTA0YjQwMDAxMTM3MjQzYSIsImlzX2RlbGV0ZWQiOmZhbHNlLCJpc0ludGVybmFsIjp"
    "0cnVlLCJyb2xlIjoiYWNjb3VudF9zdHJhdGVneSIsInNvdXJjZSI6IiIsInVpZCI6IjAwMDAwMDAwMDAwMDAwMDAwMD"
    "AwMDAwMCIsImV4cCI6MTc4NjAxODk3Nn0.3AXzcCdsTDjmKafDWx-szQz6D5w5avBG3R45_IOu92Q"
)
DASHBOARD_API_URL = "https://pub-gateway-api.vungle.com/query"
DASHBOARD_HEADERS = {
    "authorization": f"Bearer {DASHBOARD_API_TOKEN}",
    "content-type": "application/json",
    "accept": "application/json, text/plain, */*",
    "vungle-source": "admin",
    "vungle-version": "1",
}

ACCOUNT_BLOCKLIST_QUERY = """
query account($id: String!) {
  account(id: $id) {
    id
    adDomainBlacklist
    adMarketIdBlacklist { ios android }
  }
}
"""

APP_BLOCKLIST_QUERY = """
query application($id: String!) {
  application(id: $id) {
    id
    owner
    adDomainBlacklist
    marketIdFilters { blacklist }
  }
}
"""


def log(msg, level="INFO"):
    from datetime import datetime

    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def norm_domain(val):
    return str(val or "").strip().lower()


def norm_market(val):
    return str(val or "").strip()


def parse_json_list(val):
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


def parse_exemptions(val):
    return parse_json_list(val)


def flatten_account_markets(market_obj):
    if not market_obj:
        return set()
    out = set()
    for key in ("ios", "android"):
        for x in market_obj.get(key) or []:
            s = norm_market(x)
            if s:
                out.add(s)
    return out


def load_dk_domains():
    domains = set()
    if os.path.exists(COMBOS_CSV):
        combos = pd.read_csv(COMBOS_CSV, dtype=str)
        domains.update(norm_domain(d) for d in combos["domain"] if norm_domain(d))
    for d in ["draftkings.com", "casino.draftkings.com", "sportsbook.draftkings.com"]:
        domains.add(d)
    return {d for d in domains if d and "draftkings" in d}


def load_dk_markets():
    return set(DK_MARKET_IDS)


def is_draftkings_exemption(exemption, combo_domains):
    if str(exemption.get("advCatId", "")).strip().upper() != GAMBLING_CAT:
        return False
    domain = norm_domain(exemption.get("domain"))
    if not domain:
        return False
    return DK_DOMAIN_HINT in domain or domain in combo_domains


def has_draftkings_exemption(exemptions, combo_domains):
    return any(is_draftkings_exemption(ex, combo_domains) for ex in exemptions)


def dk_exempted_domains(exemptions, combo_domains):
    return sorted(
        {
            norm_domain(ex.get("domain"))
            for ex in exemptions
            if is_draftkings_exemption(ex, combo_domains)
        }
    )


def load_supply_lookup():
    frames = []
    for path in SUPPLY_CACHES:
        if os.path.exists(path):
            df = pd.read_csv(path, dtype=str)
            df["daily_spend"] = pd.to_numeric(df.get("daily_spend"), errors="coerce").fillna(0)
            frames.append(df)
    if not frames:
        return {}

    merged = pd.concat(frames, ignore_index=True)
    for col in ["account_id", "app_id", "account_name", "app_name", "pub_market_id", "region", "am_name"]:
        if col in merged.columns:
            merged[col] = merged[col].fillna("").astype(str).str.strip()
    merged = merged.sort_values("daily_spend", ascending=False)
    merged = merged.drop_duplicates(subset=["app_id"], keep="first")

    lookup = {}
    for _, row in merged.iterrows():
        app_id = str(row["app_id"]).strip()
        lookup[app_id] = {
            "account_id": str(row.get("account_id", "")).strip(),
            "account_name": str(row.get("account_name", "")).strip(),
            "app_name": str(row.get("app_name", "")).strip(),
            "pub_market_id": str(row.get("pub_market_id", "")).strip(),
            "region": str(row.get("region", "")).strip(),
            "am_name": str(row.get("am_name", "")).strip(),
            "daily_spend": float(row.get("daily_spend", 0) or 0),
        }
    return lookup


def load_spend_lookups():
    domain_lookup = {}
    market_lookup = {}
    network_total = 0.0

    if os.path.exists(DOMAIN_SPEND_CACHE):
        df = pd.read_csv(DOMAIN_SPEND_CACHE)
        df["daily_spend"] = pd.to_numeric(df["daily_spend"], errors="coerce").fillna(0)
        for _, row in df.iterrows():
            key = norm_domain(row.get("lookup_key", row.get("adomain", "")))
            domain_lookup[key] = {
                "label": str(row.get("adomain", key)).strip(),
                "daily_spend": float(row["daily_spend"]),
            }

    if os.path.exists(MARKET_SPEND_CACHE):
        df = pd.read_csv(MARKET_SPEND_CACHE)
        df["daily_spend"] = pd.to_numeric(df["daily_spend"], errors="coerce").fillna(0)
        for _, row in df.iterrows():
            key = norm_market(row.get("lookup_key", row.get("adv_bundle", "")))
            market_lookup[key] = {
                "label": str(row.get("adv_bundle", key)).strip(),
                "adv_title": str(row.get("adv_title", "")).strip(),
                "daily_spend": float(row["daily_spend"]),
            }

    if os.path.exists(NETWORK_TOTAL_CACHE):
        network_total = float(pd.read_csv(NETWORK_TOTAL_CACHE)["total_daily_spend"].iloc[0])

    return domain_lookup, market_lookup, network_total


def fetch_account_blocks_from_api(account_id):
    try:
        resp = requests.post(
            DASHBOARD_API_URL,
            headers=DASHBOARD_HEADERS,
            json={
                "operationName": "account",
                "variables": {"id": str(account_id).strip()},
                "query": ACCOUNT_BLOCKLIST_QUERY,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return str(account_id).strip(), set(), set()
        account = resp.json().get("data", {}).get("account") or {}
        domains = {norm_domain(d) for d in (account.get("adDomainBlacklist") or []) if str(d).strip()}
        markets = flatten_account_markets(account.get("adMarketIdBlacklist"))
        return str(account_id).strip(), domains, markets
    except Exception as e:
        log(f"Error fetching account {account_id}: {e}", "WARN")
        return str(account_id).strip(), set(), set()


def fetch_app_blocks_from_api(app_id, account_domains, account_markets):
    try:
        resp = requests.post(
            DASHBOARD_API_URL,
            headers=DASHBOARD_HEADERS,
            json={
                "operationName": "application",
                "variables": {"id": str(app_id).strip()},
                "query": APP_BLOCKLIST_QUERY,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return str(app_id).strip(), "", set(), set()
        app = resp.json().get("data", {}).get("application") or {}
        app_domains_all = {
            norm_domain(d) for d in (app.get("adDomainBlacklist") or []) if str(d).strip()
        }
        app_only_domains = app_domains_all - account_domains
        app_markets_raw = {
            norm_market(x)
            for x in ((app.get("marketIdFilters") or {}).get("blacklist") or [])
            if str(x).strip()
        }
        app_only_markets = app_markets_raw - account_markets
        return (
            str(app_id).strip(),
            str(app.get("owner") or "").strip(),
            app_only_domains,
            app_only_markets,
        )
    except Exception as e:
        log(f"Error fetching app {app_id}: {e}", "WARN")
        return str(app_id).strip(), "", set(), set()


def load_seed_account_blocks():
    domains_by_account = {}
    markets_by_account = {}
    if not os.path.exists(PUB_DOMAIN_ACCOUNT_BLOCKS):
        return domains_by_account, markets_by_account

    df = pd.read_csv(PUB_DOMAIN_ACCOUNT_BLOCKS, dtype=str)
    for _, row in df.iterrows():
        account_id = str(row["account_id"]).strip()
        domains_by_account[account_id] = {norm_domain(d) for d in parse_json_list(row.get("blocked_domains"))}
        markets_by_account[account_id] = {norm_market(m) for m in parse_json_list(row.get("blocked_market_ids"))}
    return domains_by_account, markets_by_account


def load_seed_app_blocks():
    app_only_domains = {}
    app_only_markets = {}
    if not os.path.exists(PUB_DOMAIN_APP_BLOCKS):
        return app_only_domains, app_only_markets

    df = pd.read_csv(PUB_DOMAIN_APP_BLOCKS, dtype=str)
    for _, row in df.iterrows():
        app_id = str(row["app_id"]).strip()
        app_only_domains[app_id] = {norm_domain(d) for d in parse_json_list(row.get("app_only_domains"))}
        app_only_markets[app_id] = {norm_market(m) for m in parse_json_list(row.get("app_only_market_ids"))}
    return app_only_domains, app_only_markets


def ensure_account_blocks(account_ids, refresh=False):
    if not refresh and os.path.exists(ACCOUNT_BLOCKS_CACHE):
        df = pd.read_csv(ACCOUNT_BLOCKS_CACHE, dtype=str)
        domains_by_account = {}
        markets_by_account = {}
        for _, row in df.iterrows():
            account_id = str(row["account_id"]).strip()
            domains_by_account[account_id] = {norm_domain(d) for d in parse_json_list(row.get("blocked_domains"))}
            markets_by_account[account_id] = {norm_market(m) for m in parse_json_list(row.get("blocked_market_ids"))}
        log(f"Account blocks: {len(domains_by_account):,} accounts from cache")
        return domains_by_account, markets_by_account

    seed_domains, seed_markets = load_seed_account_blocks()
    domains_by_account = {aid: set(v) for aid, v in seed_domains.items()}
    markets_by_account = {aid: set(v) for aid, v in seed_markets.items()}

    missing = [aid for aid in sorted(account_ids) if aid not in domains_by_account]
    if missing:
        log(f"Fetching account blocks for {len(missing):,} DK accounts via API...", "STEP")
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = [pool.submit(fetch_account_blocks_from_api, aid) for aid in missing]
            for i, future in enumerate(as_completed(futures), 1):
                account_id, domains, markets = future.result()
                domains_by_account[account_id] = domains
                markets_by_account[account_id] = markets
                if i % 20 == 0 or i == len(missing):
                    log(f"  Account blocks progress: {i}/{len(missing)}")

    rows = []
    for account_id in sorted(account_ids):
        rows.append(
            {
                "account_id": account_id,
                "blocked_domains": json.dumps(sorted(domains_by_account.get(account_id, set()))),
                "blocked_market_ids": json.dumps(sorted(markets_by_account.get(account_id, set()))),
            }
        )
    pd.DataFrame(rows).to_csv(ACCOUNT_BLOCKS_CACHE, index=False)
    log(f"Account blocks cached → {ACCOUNT_BLOCKS_CACHE}")
    return domains_by_account, markets_by_account


def ensure_app_blocks(dk_apps_df, domains_by_account, markets_by_account, refresh=False):
    app_ids = set(dk_apps_df["app_id"].astype(str).str.strip())

    if not refresh and os.path.exists(APP_BLOCKS_CACHE):
        df = pd.read_csv(APP_BLOCKS_CACHE, dtype=str)
        cached_ids = set(df["app_id"].astype(str).str.strip())
        if app_ids <= cached_ids:
            app_only_domains = {}
            app_only_markets = {}
            for _, row in df.iterrows():
                app_id = str(row["app_id"]).strip()
                app_only_domains[app_id] = {norm_domain(d) for d in parse_json_list(row.get("app_only_domains"))}
                app_only_markets[app_id] = {norm_market(m) for m in parse_json_list(row.get("app_only_market_ids"))}
            log(f"App blocks: {len(app_only_domains):,} apps from cache")
            return app_only_domains, app_only_markets

    seed_domains, seed_markets = load_seed_app_blocks()
    app_only_domains = {aid: set(v) for aid, v in seed_domains.items()}
    app_only_markets = {aid: set(v) for aid, v in seed_markets.items()}

    missing = sorted(app_ids - set(app_only_domains.keys()))
    if missing:
        log(f"Fetching app-only blocks for {len(missing):,} DK-exempt apps via API...", "STEP")

        def fetch_one(app_id):
            account_id = str(dk_apps_df.loc[dk_apps_df["app_id"] == app_id, "account_id"].iloc[0]).strip()
            acct_domains = domains_by_account.get(account_id, set())
            acct_markets = markets_by_account.get(account_id, set())
            time.sleep(API_SLEEP_SEC)
            return fetch_app_blocks_from_api(app_id, acct_domains, acct_markets)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(fetch_one, app_id): app_id for app_id in missing}
            for i, future in enumerate(as_completed(futures), 1):
                app_id, owner, domains, markets = future.result()
                app_only_domains[app_id] = domains
                app_only_markets[app_id] = markets
                if owner and app_id in dk_apps_df["app_id"].astype(str).values:
                    pass
                if i % 20 == 0 or i == len(missing):
                    log(f"  App blocks progress: {i}/{len(missing)}")

    app_account_map = dict(zip(dk_apps_df["app_id"].astype(str).str.strip(), dk_apps_df["account_id"].astype(str).str.strip()))
    rows = []
    for app_id in sorted(app_ids):
        rows.append(
            {
                "app_id": app_id,
                "account_id": app_account_map.get(app_id, ""),
                "app_only_domains": json.dumps(sorted(app_only_domains.get(app_id, set()))),
                "app_only_market_ids": json.dumps(sorted(app_only_markets.get(app_id, set()))),
            }
        )
    pd.DataFrame(rows).to_csv(APP_BLOCKS_CACHE, index=False)
    log(f"App blocks cached → {APP_BLOCKS_CACHE}")
    return app_only_domains, app_only_markets


def build_conflicts(
    dk_apps_df,
    combo_domains,
    domains_by_account,
    markets_by_account,
    app_only_domains,
    app_only_markets,
    supply_lookup,
    domain_lookup,
    market_lookup,
    network_total,
):
    log("Building exemption vs block conflict audit...", "STEP")
    dk_domains = load_dk_domains()
    dk_markets = load_dk_markets()
    rows = []

    for _, app_row in dk_apps_df.iterrows():
        app_id = str(app_row["app_id"]).strip()
        account_id = str(app_row["account_id"]).strip()
        exemptions = app_row["exemptions"]
        exempted_domains = dk_exempted_domains(exemptions, combo_domains)
        meta = supply_lookup.get(app_id, {})
        pub_daily = float(meta.get("daily_spend", 0) or 0)

        acct_domains = domains_by_account.get(account_id, set())
        acct_markets = markets_by_account.get(account_id, set())
        app_domains = app_only_domains.get(app_id, set())
        app_markets = app_only_markets.get(app_id, set())

        def append_conflict(*, level, block_type, block_value, adv_title="", adv_daily=0):
            share = adv_daily / network_total if network_total > 0 else 0
            rows.append(
                {
                    "pub_account": meta.get("account_name", ""),
                    "pub_account_id": account_id,
                    "region": meta.get("region", ""),
                    "am_name": meta.get("am_name", ""),
                    "pub_app": meta.get("app_name", ""),
                    "pub_app_id": app_id,
                    "pub_market_id": meta.get("pub_market_id", ""),
                    "pub_daily_spend": round(pub_daily, 4),
                    "exempted_dk_domains": ",".join(exempted_domains),
                    "block_level": level,
                    "block_type": block_type,
                    "block_value": block_value,
                    "adv_title": adv_title,
                    "adv_daily_spend": round(adv_daily, 4),
                    "network_share": round(share, 8),
                    "est_uplift_daily": round(pub_daily * share, 4),
                }
            )

        for domain in sorted(dk_domains & acct_domains):
            info = domain_lookup.get(domain, {})
            append_conflict(
                level="account",
                block_type="domain",
                block_value=info.get("label", domain),
                adv_daily=float(info.get("daily_spend", 0) or 0),
            )

        for market in sorted(dk_markets & acct_markets):
            info = market_lookup.get(market, {})
            append_conflict(
                level="account",
                block_type="market id",
                block_value=info.get("label", market),
                adv_title=info.get("adv_title", ""),
                adv_daily=float(info.get("daily_spend", 0) or 0),
            )

        for domain in sorted(dk_domains & app_domains):
            info = domain_lookup.get(domain, {})
            append_conflict(
                level="app",
                block_type="domain",
                block_value=info.get("label", domain),
                adv_daily=float(info.get("daily_spend", 0) or 0),
            )

        for market in sorted(dk_markets & app_markets):
            info = market_lookup.get(market, {})
            append_conflict(
                level="app",
                block_type="market id",
                block_value=info.get("label", market),
                adv_title=info.get("adv_title", ""),
                adv_daily=float(info.get("daily_spend", 0) or 0),
            )

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("est_uplift_daily", ascending=False).reset_index(drop=True)
    out.to_csv(OUTPUT_FILE, index=False)
    log(f"Conflict rows → {OUTPUT_FILE} ({len(out):,} rows)")

    if out.empty:
        rollup = pd.DataFrame()
    else:
        app_cols = [
            "pub_account",
            "pub_account_id",
            "region",
            "am_name",
            "pub_app",
            "pub_app_id",
            "pub_market_id",
            "pub_daily_spend",
            "exempted_dk_domains",
        ]
        rollup = (
            out.groupby(app_cols, as_index=False)
            .agg(
                conflict_count=("block_value", "count"),
                account_level_conflicts=("block_level", lambda s: int((s == "account").sum())),
                app_level_conflicts=("block_level", lambda s: int((s == "app").sum())),
                est_uplift_daily=("est_uplift_daily", "sum"),
            )
        )
        blocked_summary = []
        for app_id, grp in out.groupby("pub_app_id"):
            domains = sorted(grp.loc[grp["block_type"] == "domain", "block_value"].unique())
            markets = sorted(grp.loc[grp["block_type"] == "market id", "block_value"].unique())
            blocked_summary.append(
                {
                    "pub_app_id": app_id,
                    "blocked_domains": ",".join(domains),
                    "blocked_market_ids": ",".join(markets),
                }
            )
        rollup = rollup.merge(pd.DataFrame(blocked_summary), on="pub_app_id", how="left")
        rollup = rollup.sort_values("est_uplift_daily", ascending=False).reset_index(drop=True)
    rollup.to_csv(OUTPUT_BY_APP, index=False)
    log(f"App rollup → {OUTPUT_BY_APP} ({len(rollup):,} rows)")
    return out, rollup


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    refresh = "--refresh-api" in sys.argv

    if not os.path.exists(EXEMPTIONS_CACHE):
        raise FileNotFoundError(
            f"Missing {EXEMPTIONS_CACHE}. Run audit_draftkings_exemption_gaps.py first."
        )

    combo_domains = load_dk_domains()
    exemptions_df = pd.read_csv(EXEMPTIONS_CACHE, dtype=str)
    exemptions_df["exemptions"] = exemptions_df["exemptions"].apply(parse_exemptions)
    exemptions_df["app_id"] = exemptions_df["app_id"].astype(str).str.strip()
    exemptions_df["account_id"] = exemptions_df["account_id"].astype(str).str.strip()

    dk_apps_df = exemptions_df[
        exemptions_df["exemptions"].apply(lambda ex: has_draftkings_exemption(ex, combo_domains))
    ].copy()
    log(f"DK-exempted apps: {len(dk_apps_df):,} across {dk_apps_df['account_id'].nunique():,} accounts")

    supply_lookup = load_supply_lookup()
    domain_lookup, market_lookup, network_total = load_spend_lookups()
    if network_total:
        log(f"Network total daily spend: ${network_total:,.2f}")

    account_ids = set(dk_apps_df["account_id"].unique())
    domains_by_account, markets_by_account = ensure_account_blocks(account_ids, refresh=refresh)
    app_only_domains, app_only_markets = ensure_app_blocks(
        dk_apps_df, domains_by_account, markets_by_account, refresh=refresh
    )

    output, rollup = build_conflicts(
        dk_apps_df,
        combo_domains,
        domains_by_account,
        markets_by_account,
        app_only_domains,
        app_only_markets,
        supply_lookup,
        domain_lookup,
        market_lookup,
        network_total,
    )

    log("═" * 60, "STEP")
    log("DRAFTKINGS EXEMPTION vs BLOCK CONFLICT AUDIT COMPLETE", "STEP")
    log(f"Conflict rows: {len(output):,}")
    log(f"Apps with ≥1 conflict: {len(rollup):,}")
    if not output.empty:
        print("\nTop 10 conflicts by est_uplift_daily:")
        print(
            output[
                [
                    "pub_account",
                    "pub_app",
                    "block_level",
                    "block_type",
                    "block_value",
                    "est_uplift_daily",
                ]
            ]
            .head(10)
            .to_string(index=False)
        )
    log("═" * 60, "STEP")


if __name__ == "__main__":
    main()
