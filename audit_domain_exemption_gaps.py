"""
Domain Exemption Gap Audit — app-level summary

For high-spend publisher apps (≥$500/day) with at least one domain
blocklistExemption, compares configured routes (domain × RTB × geo) against
network-wide high-spend routes (≥$500/day) for the same exempted domains.

Blank exemption country = all geos for that domain+RTB.

Penetration-based uplift:
  penetration_rate        = app_daily_spend_exempted / exempted_route_network_daily
  est_uplift_missed_daily = penetration_rate × missing route network spend

Output:
  output/domain_exemption_audit/audit_domain_exemption_gaps_missing_combos.csv
  output/domain_exemption_audit/audit_domain_exemption_gaps_by_app.csv
"""

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

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

LOOKBACK_DAYS = 7
SPEND_THRESHOLD = 500
ROUTE_MIN_DAILY = 500
TOTAL_THRESHOLD = SPEND_THRESHOLD * LOOKBACK_DAYS
ROUTE_TOTAL_THRESHOLD = ROUTE_MIN_DAILY * LOOKBACK_DAYS
PAGE_SIZE = 50_000
ROUTE_LOOKER_LIMIT = 100_000
APP_BATCH = 100
MAX_WORKERS = 25

LOOKER_FIELD_ADOMAIN = "vx_overview.adomain"
LOOKER_FIELD_GEO = "geos.code"
LOOKER_FIELD_RTB = "rtb_accounts.id"
LOOKER_FIELD_SPEND = "vx_overview.unified_ad_spend"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output", "domain_exemption_audit")
SUPPLY_CACHE = os.path.join(SCRIPT_DIR, "output", "pub_blocked_categories", "p1_apps.csv")
DOMAIN_SUPPLY_CACHE = os.path.join(SCRIPT_DIR, "output", "pub_blocked_domains", "p1_apps.csv")
NETWORK_TOTAL_CACHE = os.path.join(SCRIPT_DIR, "output", "pub_blocked_domains", "p3_network_total.csv")

os.makedirs(OUTPUT_DIR, exist_ok=True)

CACHE_APPS = f"{OUTPUT_DIR}/p1_apps.csv"
CACHE_EXEMPTIONS = f"{OUTPUT_DIR}/p2_app_exemptions.csv"
CACHE_FETCHED = f"{OUTPUT_DIR}/p2_apps_fetched.csv"
CACHE_DONE = f"{CACHE_EXEMPTIONS}.done"
CACHE_NETWORK_ROUTES = f"{OUTPUT_DIR}/p3_network_routes.csv"
CACHE_APP_ROUTE_SPEND = f"{OUTPUT_DIR}/p3_app_route_spend.csv"
OUTPUT_FILE_COMBOS = f"{OUTPUT_DIR}/audit_domain_exemption_gaps_missing_combos.csv"
OUTPUT_FILE = f"{OUTPUT_DIR}/audit_domain_exemption_gaps_by_app.csv"

APP_QUERY = """
query application($id: String!) {
  application(id: $id) {
    id
    owner
    blocklistExemptions {
      advCatId
      domain
      bundle
      rtbAccountId
      country
    }
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


def norm_rtb(val):
    return str(val or "").strip()


def norm_country(val):
    return str(val or "").strip().upper()


def route_key(domain, rtb_account_id, country):
    return (norm_domain(domain), norm_rtb(rtb_account_id), norm_country(country))


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


def looker_rows_to_df(raw, rename_map):
    if not raw:
        return pd.DataFrame(columns=list(rename_map.values()))
    df = pd.DataFrame(raw)
    missing = [src for src in rename_map if src not in df.columns]
    if missing:
        raise RuntimeError(f"Looker response missing expected fields: {missing}")
    return df.rename(columns=rename_map)


def get_looker_token():
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}",
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def run_looker_query(token, model, view, fields, filters, sorts=None, limit=PAGE_SIZE):
    payload = {
        "model": model,
        "view": view,
        "fields": fields,
        "filters": filters,
        "limit": str(limit),
    }
    if sorts:
        payload["sorts"] = sorts
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers={"Authorization": f"token {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=600,
    )
    if not resp.ok:
        log(f"Looker error {resp.status_code}: {resp.text[:500]}", "ERROR")
        resp.raise_for_status()
    return resp.json()


def fetch_supply(token=None):
    if os.path.exists(CACHE_APPS) and "--refresh-supply" not in sys.argv:
        df = pd.read_csv(CACHE_APPS, dtype=str)
        df["daily_spend"] = pd.to_numeric(df["daily_spend"], errors="coerce").fillna(0)
        log(f"Supply: {len(df):,} apps from cache")
        return df

    for path in [SUPPLY_CACHE, DOMAIN_SUPPLY_CACHE]:
        if os.path.exists(path):
            df = pd.read_csv(path, dtype=str)
            df["daily_spend"] = pd.to_numeric(df["daily_spend"], errors="coerce").fillna(0)
            df.to_csv(CACHE_APPS, index=False)
            log(f"Supply: {len(df):,} apps (from {path})")
            return df

    if not token:
        token = get_looker_token()
    fields = [
        "publisher_accounts.id",
        "publisher_accounts.name",
        "publisher_apps.id",
        "publisher_apps.name",
        "publisher_apps.market_id",
        "salesforce_accounts_monetize.am_user_region",
        "salesforce_accounts_monetize.am_user_name",
        "publisher_report.unified_ad_spend",
    ]
    filters = {
        "publisher_report.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
        "publisher_report.unified_ad_spend": f">={TOTAL_THRESHOLD}",
    }
    raw = run_looker_query(
        token,
        "dmx_reports",
        "publisher_report",
        fields,
        filters,
        sorts=["publisher_report.unified_ad_spend desc"],
    )
    raw_df = pd.DataFrame(raw)
    raw_df.columns = [
        "account_id", "account_name", "app_id", "app_name", "pub_market_id",
        "region", "am_name", "total_spend_7d",
    ]
    for col in ["account_id", "app_id", "account_name", "app_name", "pub_market_id", "region", "am_name"]:
        raw_df[col] = raw_df[col].fillna("").astype(str).str.strip()
    raw_df["total_spend_7d"] = pd.to_numeric(raw_df["total_spend_7d"], errors="coerce").fillna(0)
    raw_df = (
        raw_df.groupby(
            ["account_id", "account_name", "app_id", "app_name", "pub_market_id", "region", "am_name"],
            dropna=False,
        )
        .agg(total_spend_7d=("total_spend_7d", "sum"))
        .reset_index()
    )
    raw_df["daily_spend"] = (raw_df["total_spend_7d"] / LOOKBACK_DAYS).round(4)
    raw_df = raw_df[raw_df["daily_spend"] >= SPEND_THRESHOLD].copy()
    raw_df.to_csv(CACHE_APPS, index=False)
    log(f"Supply: {len(raw_df):,} qualifying apps")
    return raw_df


def fetch_app_exemptions(app_id):
    try:
        resp = requests.post(
            DASHBOARD_API_URL,
            headers=DASHBOARD_HEADERS,
            json={
                "operationName": "application",
                "variables": {"id": str(app_id).strip()},
                "query": APP_QUERY,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            return None
        app = resp.json().get("data", {}).get("application")
        if not app:
            return None
        return {
            "app_id": str(app_id).strip(),
            "account_id": str(app.get("owner") or "").strip(),
            "exemptions": app.get("blocklistExemptions") or [],
        }
    except Exception:
        return None


def fetch_all_exemptions(apps_df):
    if os.path.exists(CACHE_DONE) and os.path.exists(CACHE_EXEMPTIONS) and "--refresh-api" not in sys.argv:
        df = pd.read_csv(CACHE_EXEMPTIONS, dtype=str)
        df["exemptions"] = df["exemptions"].apply(parse_exemptions)
        log(f"Exemptions cache: {len(df):,} apps")
        return df

    already_done = set()
    rows = []
    if os.path.exists(CACHE_EXEMPTIONS):
        partial = pd.read_csv(CACHE_EXEMPTIONS, dtype=str)
        if not partial.empty:
            rows = partial.to_dict("records")
    if os.path.exists(CACHE_FETCHED):
        already_done = set(pd.read_csv(CACHE_FETCHED, dtype=str)["app_id"].astype(str).str.strip())

    app_ids = apps_df["app_id"].astype(str).str.strip().tolist()
    remaining = [a for a in app_ids if a not in already_done]
    total = len(app_ids)
    log(f"Fetching blocklistExemptions for {len(remaining):,} remaining of {total:,} apps...")

    def flush():
        out = pd.DataFrame(rows)
        if not out.empty:
            out["exemptions"] = out["exemptions"].apply(
                lambda x: json.dumps(x) if isinstance(x, list) else x
            )
            out.to_csv(CACHE_EXEMPTIONS, index=False)
        pd.DataFrame({"app_id": sorted(already_done)}).to_csv(CACHE_FETCHED, index=False)

    completed = len(already_done)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fetch_app_exemptions, app_id): app_id for app_id in remaining}
        for i, future in enumerate(as_completed(futures), 1):
            app_id = futures[future]
            already_done.add(app_id)
            result = future.result()
            if result:
                rows.append(result)
            completed += 1
            if i % 100 == 0 or i == len(remaining):
                log(f"  Progress: {completed}/{total} apps...")
                flush()

    flush()
    with open(CACHE_DONE, "w") as f:
        f.write("done\n")

    df = pd.read_csv(CACHE_EXEMPTIONS, dtype=str)
    df["exemptions"] = df["exemptions"].apply(parse_exemptions)
    log(f"Fetched exemptions for {len(df):,} apps")
    return df


def fetch_network_routes(token):
    if os.path.exists(CACHE_NETWORK_ROUTES) and "--refresh-routes" not in sys.argv:
        df = pd.read_csv(CACHE_NETWORK_ROUTES, dtype=str)
        df["network_daily_spend"] = pd.to_numeric(df["network_daily_spend"], errors="coerce").fillna(0)
        log(f"Network routes cache: {len(df):,} routes ≥ ${ROUTE_MIN_DAILY:,}/day")
        return df

    log(f"Fetching network routes (domain × RTB × geo, ≥ ${ROUTE_MIN_DAILY:,}/day)...", "STEP")
    date_filter = f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
    fields = [LOOKER_FIELD_ADOMAIN, LOOKER_FIELD_RTB, LOOKER_FIELD_GEO, LOOKER_FIELD_SPEND]
    raw = run_looker_query(
        token,
        "vx_analytics",
        "vx_overview",
        fields,
        {
            "vx_overview.event_date": date_filter,
            "vx_overview.unified_ad_spend": f">={ROUTE_TOTAL_THRESHOLD}",
        },
        sorts=[f"{LOOKER_FIELD_SPEND} desc"],
        limit=ROUTE_LOOKER_LIMIT,
    )
    if not raw:
        raise RuntimeError("No route spend rows returned from vx_overview")

    df = looker_rows_to_df(
        raw,
        {
            LOOKER_FIELD_ADOMAIN: "domain",
            LOOKER_FIELD_RTB: "rtb_account_id",
            LOOKER_FIELD_GEO: "country",
            LOOKER_FIELD_SPEND: "network_spend_7d",
        },
    )
    df["network_spend_7d"] = pd.to_numeric(df["network_spend_7d"], errors="coerce").fillna(0)
    for col in ["domain", "rtb_account_id", "country"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df["domain"] = df["domain"].str.lower()
    df = df[df["domain"].ne("") & df["rtb_account_id"].ne("") & df["country"].ne("")].copy()
    df = (
        df.groupby(["domain", "rtb_account_id", "country"], as_index=False)
        .agg(network_spend_7d=("network_spend_7d", "sum"))
    )
    df["network_daily_spend"] = (df["network_spend_7d"] / LOOKBACK_DAYS).round(4)
    df = df[df["network_daily_spend"] >= ROUTE_MIN_DAILY].copy()
    df["route_key"] = df.apply(
        lambda r: route_key(r["domain"], r["rtb_account_id"], r["country"]), axis=1
    )
    df = df.sort_values("network_daily_spend", ascending=False).reset_index(drop=True)
    df.to_csv(CACHE_NETWORK_ROUTES, index=False)
    log(f"  {len(df):,} network routes ≥ ${ROUTE_MIN_DAILY:,}/day → {CACHE_NETWORK_ROUTES}")
    return df


def fetch_app_route_spend(token, app_ids, network_routes):
    valid_keys = set(network_routes["route_key"])
    if os.path.exists(CACHE_APP_ROUTE_SPEND) and "--refresh-app-spend" not in sys.argv:
        df = pd.read_csv(CACHE_APP_ROUTE_SPEND, dtype=str)
        df["daily_spend"] = pd.to_numeric(df["daily_spend"], errors="coerce").fillna(0)
        log(f"App route spend cache: {len(df):,} rows")
        return df

    if not app_ids:
        return pd.DataFrame(
            columns=["app_id", "domain", "rtb_account_id", "country", "daily_spend", "route_key"]
        )

    date_filter = f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"
    fields = [
        "publisher_apps.id",
        LOOKER_FIELD_ADOMAIN,
        LOOKER_FIELD_RTB,
        LOOKER_FIELD_GEO,
        LOOKER_FIELD_SPEND,
    ]
    app_ids = [str(a).strip() for a in app_ids]
    batches = [app_ids[i : i + APP_BATCH] for i in range(0, len(app_ids), APP_BATCH)]
    log(f"Fetching app route spend ({len(app_ids)} apps with domain exemptions)...", "STEP")

    all_rows = []
    for idx, batch in enumerate(batches, 1):
        raw = run_looker_query(
            token,
            "vx_analytics",
            "vx_overview",
            fields,
            {
                "vx_overview.event_date": date_filter,
                "publisher_apps.id": ",".join(batch),
            },
            sorts=[f"{LOOKER_FIELD_SPEND} desc"],
            limit=PAGE_SIZE,
        )
        all_rows.extend(raw or [])
        log(f"  Batch {idx}/{len(batches)}: {len(all_rows):,} rows so far")

    if not all_rows:
        df = pd.DataFrame(
            columns=["app_id", "domain", "rtb_account_id", "country", "daily_spend", "route_key"]
        )
    else:
        df = looker_rows_to_df(
            all_rows,
            {
                "publisher_apps.id": "app_id",
                LOOKER_FIELD_ADOMAIN: "domain",
                LOOKER_FIELD_RTB: "rtb_account_id",
                LOOKER_FIELD_GEO: "country",
                LOOKER_FIELD_SPEND: "total_spend_7d",
            },
        )
        df["total_spend_7d"] = pd.to_numeric(df["total_spend_7d"], errors="coerce").fillna(0)
        for col in ["app_id", "domain", "rtb_account_id", "country"]:
            df[col] = df[col].fillna("").astype(str).str.strip()
        df["domain"] = df["domain"].str.lower()
        df = df.groupby(["app_id", "domain", "rtb_account_id", "country"], as_index=False).agg(
            total_spend_7d=("total_spend_7d", "sum")
        )
        df["daily_spend"] = (df["total_spend_7d"] / LOOKBACK_DAYS).round(4)
        df["route_key"] = df.apply(
            lambda r: route_key(r["domain"], r["rtb_account_id"], r["country"]), axis=1
        )
        df = df[df["route_key"].isin(valid_keys)].copy()

    df.to_csv(CACHE_APP_ROUTE_SPEND, index=False)
    log(f"  Saved app route spend → {CACHE_APP_ROUTE_SPEND} ({len(df):,} rows)")
    return df


def exemption_domains(exemptions):
    return {
        norm_domain(ex.get("domain"))
        for ex in exemptions
        if norm_domain(ex.get("domain"))
    }


def has_domain_exemption(exemptions):
    return bool(exemption_domains(exemptions))


def route_is_covered(exemptions, route):
    target_domain = norm_domain(route["domain"])
    target_rtb = norm_rtb(route["rtb_account_id"])
    target_country = norm_country(route["country"])

    for ex in exemptions:
        if norm_domain(ex.get("domain")) != target_domain:
            continue
        if norm_rtb(ex.get("rtbAccountId")) != target_rtb:
            continue
        ex_country = norm_country(ex.get("country"))
        if ex_country == "" or ex_country == target_country:
            return True
    return False


def load_network_total():
    if os.path.exists(NETWORK_TOTAL_CACHE):
        return float(pd.read_csv(NETWORK_TOTAL_CACHE)["total_daily_spend"].iloc[0])
    return 0.0


def build_missing_combos_audit(apps_df, exemptions_df, network_routes, network_total):
    log("Building missing-combo audit (one row per exempted domain × missing route)...", "STEP")
    log("  Blank exemption country = all geos for that domain+RTB", "STEP")

    routes_by_domain = network_routes.groupby("domain")
    app_meta = apps_df.set_index("app_id", drop=False)
    app_meta.index = app_meta.index.astype(str).str.strip()

    rows = []
    for _, row in exemptions_df.iterrows():
        app_id = str(row["app_id"]).strip()
        exemptions = row.get("exemptions") or []
        if not has_domain_exemption(exemptions):
            continue
        if app_id not in app_meta.index:
            continue

        meta = app_meta.loc[app_id]
        pub_daily = round(float(meta.get("daily_spend", 0) or 0), 4)

        for exempted_domain in sorted(exemption_domains(exemptions)):
            if exempted_domain not in routes_by_domain.groups:
                continue

            ref_routes = routes_by_domain.get_group(exempted_domain).drop_duplicates(
                subset=["route_key"]
            )
            if ref_routes.empty:
                continue

            covered_mask = ref_routes.apply(lambda r: route_is_covered(exemptions, r), axis=1)
            covered = ref_routes[covered_mask]
            missing = ref_routes[~covered_mask]
            if missing.empty:
                continue

            missing = missing.sort_values("network_daily_spend", ascending=False)
            reference_route_count = len(ref_routes)
            exempted_route_count = len(covered)

            for _, route in missing.iterrows():
                combo_daily = float(route["network_daily_spend"])
                network_share = combo_daily / network_total if network_total > 0 else 0
                rows.append(
                    {
                        "pub_account": meta.get("account_name", ""),
                        "pub_account_id": meta.get("account_id", ""),
                        "region": meta.get("region", ""),
                        "am_name": meta.get("am_name", ""),
                        "pub_app": meta.get("app_name", ""),
                        "pub_app_id": app_id,
                        "pub_market_id": meta.get("pub_market_id", ""),
                        "pub_daily_spend": pub_daily,
                        "exempted_domain": exempted_domain,
                        "domain": route["domain"],
                        "rtb_account_id": route["rtb_account_id"],
                        "country": route["country"],
                        "combo_daily_spend": round(combo_daily, 4),
                        "network_share": round(network_share, 8),
                        "est_uplift_daily": round(network_share * pub_daily, 4),
                        "exempted_route_count": exempted_route_count,
                        "reference_route_count": reference_route_count,
                    }
                )

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("est_uplift_daily", ascending=False).reset_index(drop=True)
    out.to_csv(OUTPUT_FILE_COMBOS, index=False)
    log(f"Missing combos → {OUTPUT_FILE_COMBOS} ({len(out):,} rows)")
    return out


def build_app_summary(apps_df, exemptions_df, network_routes, app_spend_df):
    log("Building app-level domain exemption gap summary...", "STEP")
    log("  Blank exemption country = all geos for that domain+RTB", "STEP")

    routes_by_domain = network_routes.groupby("domain")
    app_meta = apps_df.set_index("app_id", drop=False)
    app_meta.index = app_meta.index.astype(str).str.strip()

    spend_by_app = {}
    if not app_spend_df.empty:
        for app_id, grp in app_spend_df.groupby("app_id"):
            spend_by_app[str(app_id).strip()] = grp

    rows = []
    apps_with_exemptions = 0
    apps_incomplete = 0

    for _, row in exemptions_df.iterrows():
        app_id = str(row["app_id"]).strip()
        exemptions = row.get("exemptions") or []
        if not has_domain_exemption(exemptions):
            continue
        apps_with_exemptions += 1

        domains = exemption_domains(exemptions)
        ref_parts = []
        for domain in domains:
            if domain in routes_by_domain.groups:
                ref_parts.append(routes_by_domain.get_group(domain))
        if not ref_parts:
            continue

        ref_routes = pd.concat(ref_parts, ignore_index=True).drop_duplicates(subset=["route_key"])
        if ref_routes.empty:
            continue

        covered_mask = ref_routes.apply(lambda r: route_is_covered(exemptions, r), axis=1)
        covered = ref_routes[covered_mask]
        missing = ref_routes[~covered_mask]
        if missing.empty:
            continue

        apps_incomplete += 1
        if app_id not in app_meta.index:
            continue
        meta = app_meta.loc[app_id]

        covered_keys = set(covered["route_key"])
        app_spend = spend_by_app.get(app_id, pd.DataFrame())
        if not app_spend.empty:
            app_spend_exempted = float(
                app_spend[app_spend["route_key"].isin(covered_keys)]["daily_spend"].sum()
            )
        else:
            app_spend_exempted = 0.0

        reference_network_daily = float(ref_routes["network_daily_spend"].sum())
        exempted_network_daily = float(covered["network_daily_spend"].sum())
        missing_network_daily = float(missing["network_daily_spend"].sum())

        penetration = (
            app_spend_exempted / exempted_network_daily if exempted_network_daily > 0 else 0
        )
        est_uplift = round(penetration * missing_network_daily, 4)

        top_missing = missing.sort_values("network_daily_spend", ascending=False).head(3)
        top_missing_str = "; ".join(
            f"{r['domain']}|{r['rtb_account_id']}|{r['country']}"
            for _, r in top_missing.iterrows()
        )

        rows.append(
            {
                "pub_account": meta.get("account_name", ""),
                "pub_account_id": meta.get("account_id", ""),
                "region": meta.get("region", ""),
                "am_name": meta.get("am_name", ""),
                "pub_app": meta.get("app_name", ""),
                "pub_app_id": app_id,
                "pub_market_id": meta.get("pub_market_id", ""),
                "pub_daily_spend": round(float(meta.get("daily_spend", 0) or 0), 4),
                "exempted_domain_count": len(domains),
                "exempted_domains": ",".join(sorted(domains)[:20]),
                "reference_route_count": len(ref_routes),
                "exempted_route_count": len(covered),
                "missing_route_count": len(missing),
                "top_missing_routes": top_missing_str,
                "app_daily_spend_exempted": round(app_spend_exempted, 4),
                "exempted_route_network_daily": round(exempted_network_daily, 4),
                "missing_route_network_daily": round(missing_network_daily, 4),
                "reference_route_network_daily": round(reference_network_daily, 4),
                "penetration_rate": round(penetration, 8),
                "est_uplift_missed_daily": est_uplift,
            }
        )

    log(f"  Apps with any domain exemption: {apps_with_exemptions:,}")
    log(f"  Apps with incomplete high-spend routes: {apps_incomplete:,}")

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("est_uplift_missed_daily", ascending=False).reset_index(drop=True)
    out.to_csv(OUTPUT_FILE, index=False)
    log(f"App summary → {OUTPUT_FILE} ({len(out):,} rows)")
    return out


def clear_caches():
    for path in [
        CACHE_APPS, CACHE_EXEMPTIONS, CACHE_FETCHED, CACHE_DONE,
        CACHE_NETWORK_ROUTES, CACHE_APP_ROUTE_SPEND, OUTPUT_FILE_COMBOS, OUTPUT_FILE,
    ]:
        if os.path.exists(path):
            os.remove(path)
            log(f"  deleted {path}")


def main():
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    if "--refresh" in sys.argv:
        clear_caches()

    token = get_looker_token()
    apps_df = fetch_supply(token)
    exemptions_df = fetch_all_exemptions(apps_df)
    network_routes = fetch_network_routes(token)

    qualifying_app_ids = []
    for _, row in exemptions_df.iterrows():
        exemptions = row.get("exemptions") or []
        if not has_domain_exemption(exemptions):
            continue
        qualifying_app_ids.append(str(row["app_id"]).strip())

    app_spend_df = fetch_app_route_spend(token, qualifying_app_ids, network_routes)
    network_total = load_network_total()
    combos_output = build_missing_combos_audit(
        apps_df, exemptions_df, network_routes, network_total
    )
    app_output = build_app_summary(apps_df, exemptions_df, network_routes, app_spend_df)

    log("═" * 60, "STEP")
    log("AUDIT COMPLETE", "STEP")
    if combos_output.empty:
        log("No missing combo rows found", "WARN")
    else:
        log(f"Missing combo rows: {len(combos_output):,}")
        log(f"App summary rows: {len(app_output):,}")
        print("\nTop 10 missing combos by est_uplift_daily:")
        print(
            combos_output[
                [
                    "pub_account", "pub_app", "exempted_domain", "domain",
                    "rtb_account_id", "country", "est_uplift_daily",
                ]
            ]
            .head(10)
            .to_string(index=False)
        )
    log("═" * 60, "STEP")


if __name__ == "__main__":
    main()
