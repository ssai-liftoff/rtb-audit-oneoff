"""
Whitelist Audit — Part 4: Fetch Category-Filtered Combo & Network Spend

For every (publisher_app, whitelisted_domain, adv_cat_id) triple in p3, computes
spend that is both:
  (a) from that specific advertiser domain on that publisher app, AND
  (b) categorised under the IAB content categories that map to the V-code

Example: Kalshi.com exempted under V1-6 (Gambling → IAB9-7).
  Only Kalshi's IAB9-7 (Card Games/Gambling) spend on that publisher app
  is counted — not Kalshi's IAB13 (Personal Finance) spend.

V-code → IAB mapping is hardcoded from the internal category reference table.
Prefix matching is used for top-level IAB codes (e.g. IAB13 matches IAB13, IAB13-2, etc.)

Strategy:
  1. Fetch (publisher_apps.id, adomain, content_category_code, spend) from Looker,
     filtered to apps with exemptions × exempt domains.
  2. In pandas: join to p3, filter rows where content_category_code falls under
     the IAB codes for adv_cat_id, then sum per (app_id, domain, adv_cat_id).
  3. Same for network-wide spend (no publisher filter).

Input:  output/whitelist_audit/p3_whitelist_audit.csv
Output: output/whitelist_audit/p4_whitelist_audit.csv
"""

import os
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL      = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID     = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

OUTPUT_DIR    = "output/whitelist_audit"
LOOKBACK_DAYS = 7
APP_BATCH     = 100
PAGE_SIZE     = 50_000

os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── V-code → IAB content category codes ──────────────────────────────────────
# Top-level codes (e.g. "IAB13") also match all sub-codes (IAB13-2, etc.)
V_CODE_TO_IAB = {
    "V1-1":  ["IAB8-5", "IAB8-18"],
    "V1-2":  ["IAB25-2"],
    "V1-3":  ["IAB13-2"],
    "V1-4":  ["IAB14-2", "IAB14-3", "IAB14-4"],
    "V1-5":  ["IAB7-3", "IAB7-5", "IAB7-28", "IAB7-29", "IAB7-30", "IAB7-41", "IAB7-42"],
    "V1-6":  ["IAB9-7"],
    "V1-7":  ["IAB11"],
    "V1-8":  [],                                              # no IAB equivalent
    "V1-9":  ["IAB7-39", "IAB14-3", "IAB18-2", "IAB25-3", "IAB9-9", "IAB8-18"],
    "V1-10": ["IAB25-1"],
    "V1-11": ["IAB23", "IAB14-8"],
    "V1-12": ["IAB7-30"],
    "V1-13": ["IAB7-39", "IAB25-3", "IAB25-4", "IAB25-5", "IAB25-6"],
    "V1-14": ["IAB26"],
    "V2-1":  ["IAB7-2", "IAB7-6", "IAB7-7", "IAB7-8", "IAB7-9", "IAB7-10",
              "IAB7-11", "IAB7-12", "IAB7-13", "IAB7-14", "IAB7-15", "IAB7-16",
              "IAB7-18", "IAB7-19", "IAB7-20", "IAB7-21", "IAB7-22", "IAB7-23",
              "IAB7-24", "IAB7-27", "IAB7-33", "IAB7-34", "IAB7-35", "IAB7-43",
              "IAB20-24"],
    "V2-2":  ["IAB12", "IAB1-2"],
    "V2-3":  ["IAB17-18"],
    "V2-4":  ["IAB14-6"],
    "V2-5":  ["IAB24"],
    "V2-6":  ["IAB17-4", "IAB17-5", "IAB17-7", "IAB17-14", "IAB17-16",
              "IAB17-20", "IAB17-24", "IAB17-28", "IAB17-29"],
    "V2-7":  ["IAB8-6", "IAB8-9"],
    "V2-8":  ["IAB6-1", "IAB6-6", "IAB6-7"],
    "V2-9":  ["IAB22-1"],
    "V2-10": ["IAB13"],
    "V2-11": ["IAB14"],
    "V2-12": ["IAB16-7"],
    "V2-13": ["IAB3-7", "IAB4-10"],
    "V2-14": ["IAB25-7"],
    "V2-15": ["IAB25"],
    "V2-16": ["IAB14-1"],
    "V2-17": ["IAB7-38", "IAB14-5"],
    "V2-18": ["IAB7-25", "IAB7-26"],
    "V2-19": ["IAB15-5"],
    "V2-20": ["IAB9-30"],
    "V2-21": ["IAB7-44"],
}


def iab_matches(iab_code, allowed_codes):
    """True if iab_code matches any code in allowed_codes (exact or prefix for top-level)."""
    if not iab_code or not allowed_codes:
        return False
    iab_code = str(iab_code).strip()
    for allowed in allowed_codes:
        if iab_code == allowed or iab_code.startswith(allowed + "-"):
            return True
    return False


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def get_token():
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}",
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def auth_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def run_query(token, fields, filters, offset=0):
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=auth_headers(token),
        json={
            "model":   "vx_analytics",
            "view":    "vx_overview",
            "fields":  fields,
            "filters": filters,
            "sorts":   ["vx_overview.unified_ad_spend desc"],
            "limit":   str(PAGE_SIZE),
            "offset":  str(offset),
        },
        timeout=300,
    )
    if not resp.ok:
        log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
        resp.raise_for_status()
    return resp.json()


def paginate(token, fields, filters, label=""):
    rows, offset = [], 0
    while True:
        page = run_query(token, fields, filters, offset)
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
        log(f"  {label} paginating… {offset:,} rows so far")
    return rows


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    cache = f"{OUTPUT_DIR}/p4_whitelist_audit.csv"

    if os.path.exists(cache):
        log(f"Cache found — loading {cache}")
        out = pd.read_csv(cache)
        log(f"  {len(out):,} rows loaded")
    else:
        log("═" * 60, "STEP")
        log("WHITELIST AUDIT — PART 4: CATEGORY-FILTERED COMBO SPEND", "STEP")
        log("═" * 60, "STEP")

        p3_path = f"{OUTPUT_DIR}/p3_whitelist_audit.csv"
        if not os.path.exists(p3_path):
            raise FileNotFoundError(f"Missing: {p3_path} — run p3_build_audit.py first")

        p3 = pd.read_csv(p3_path)
        p3["app_id"] = p3["app_id"].astype(str).str.strip()

        domain_rows = p3[p3["exempt_domain"].notna()].copy()
        exempt_domains            = domain_rows["exempt_domain"].dropna().astype(str).str.strip().unique().tolist()
        app_ids_with_exemptions   = domain_rows["app_id"].dropna().unique().tolist()

        log(f"Apps with domain exemptions:  {len(app_ids_with_exemptions):,}")
        log(f"Unique whitelisted domains:   {len(exempt_domains):,}")

        domain_filter = ",".join(exempt_domains)
        date_filter   = f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days"

        token = get_token()
        log("Authenticated with Looker")

        # ── 1. Fetch combo spend (pub app × adomain × IAB code) ──────────────
        COMBO_FIELDS = [
            "publisher_apps.id",
            "vx_overview.adomain",
            "vx_overview.content_category_code",
            "vx_overview.unified_ad_spend",
        ]

        app_batches = [
            app_ids_with_exemptions[i : i + APP_BATCH]
            for i in range(0, len(app_ids_with_exemptions), APP_BATCH)
        ]
        log(f"Fetching combo spend in {len(app_batches)} batch(es)...", "STEP")

        all_combo_rows = []
        for b_idx, batch in enumerate(app_batches, 1):
            rows = paginate(
                token,
                COMBO_FIELDS,
                {
                    "vx_overview.event_date": date_filter,
                    "publisher_apps.id":      ",".join(batch),
                    "vx_overview.adomain":    domain_filter,
                },
                label=f"combo batch {b_idx}",
            )
            all_combo_rows.extend(rows)
            log(f"  Batch {b_idx}/{len(app_batches)}: {len(batch)} apps → {len(all_combo_rows):,} rows total")

        log(f"Total combo rows (pre-IAB filter): {len(all_combo_rows):,}")

        if all_combo_rows:
            combo_raw = pd.DataFrame(all_combo_rows)
            combo_raw.columns = ["app_id", "exempt_domain", "iab_code", "spend"]
            combo_raw["app_id"]        = combo_raw["app_id"].astype(str).str.strip()
            combo_raw["exempt_domain"] = combo_raw["exempt_domain"].astype(str).str.strip()
            combo_raw["spend"]         = pd.to_numeric(combo_raw["spend"], errors="coerce").fillna(0)
        else:
            combo_raw = pd.DataFrame(columns=["app_id", "exempt_domain", "iab_code", "spend"])

        # ── 2. Fetch network spend (adomain × IAB code, no publisher filter) ─
        log("Fetching network-wide spend per domain × IAB code...", "STEP")
        NET_FIELDS = [
            "vx_overview.adomain",
            "vx_overview.content_category_code",
            "vx_overview.unified_ad_spend",
        ]
        net_rows = paginate(
            token,
            NET_FIELDS,
            {"vx_overview.event_date": date_filter, "vx_overview.adomain": domain_filter},
            label="network",
        )
        log(f"Network rows (pre-IAB filter): {len(net_rows):,}")

        if net_rows:
            net_raw = pd.DataFrame(net_rows)
            net_raw.columns = ["exempt_domain", "iab_code", "spend"]
            net_raw["exempt_domain"] = net_raw["exempt_domain"].astype(str).str.strip()
            net_raw["spend"]         = pd.to_numeric(net_raw["spend"], errors="coerce").fillna(0)
        else:
            net_raw = pd.DataFrame(columns=["exempt_domain", "iab_code", "spend"])

        # ── 3. Apply IAB filter in pandas and aggregate ───────────────────────
        log("Applying V-code → IAB category filter...", "STEP")

        # For each p3 exemption triple (app_id, domain, adv_cat_id), sum only
        # spend rows whose iab_code falls under adv_cat_id's IAB codes.
        combo_results = []
        net_results   = []

        exemption_triples = (
            domain_rows[["app_id", "exempt_domain", "adv_cat_id"]]
            .drop_duplicates()
        )

        for _, ex in exemption_triples.iterrows():
            app_id     = ex["app_id"]
            domain     = ex["exempt_domain"]
            v_code     = ex["adv_cat_id"]
            iab_codes  = V_CODE_TO_IAB.get(str(v_code).strip(), []) if pd.notna(v_code) else []

            # ── Combo spend ──
            subset = combo_raw[
                (combo_raw["app_id"] == app_id) &
                (combo_raw["exempt_domain"] == domain)
            ]
            if iab_codes:
                mask = subset["iab_code"].apply(lambda c: iab_matches(c, iab_codes))
                matched_spend = subset.loc[mask, "spend"].sum()
            else:
                matched_spend = None  # V1-8 has no IAB codes

            combo_results.append({
                "app_id":        app_id,
                "exempt_domain": domain,
                "adv_cat_id":    v_code,
                "combo_spend_7d": matched_spend,
            })

            # ── Network spend ──
            net_subset = net_raw[net_raw["exempt_domain"] == domain]
            if iab_codes:
                net_mask = net_subset["iab_code"].apply(lambda c: iab_matches(c, iab_codes))
                net_spend = net_subset.loc[net_mask, "spend"].sum()
            else:
                net_spend = None

            net_results.append({
                "exempt_domain":          domain,
                "adv_cat_id":             v_code,
                "domain_network_spend_7d": net_spend,
            })

        combo_df = pd.DataFrame(combo_results)
        net_df   = pd.DataFrame(net_results).drop_duplicates(subset=["exempt_domain", "adv_cat_id"])

        log(f"Exemption triples processed: {len(combo_df):,}")

        # ── 4. Join onto p3 ──────────────────────────────────────────────────
        out = p3.merge(combo_df, on=["app_id", "exempt_domain", "adv_cat_id"], how="left")
        out = out.merge(net_df,  on=["exempt_domain", "adv_cat_id"], how="left")

        out["combo_daily_spend"]          = (out["combo_spend_7d"] / LOOKBACK_DAYS).round(2)
        out["domain_network_daily_spend"] = (out["domain_network_spend_7d"] / LOOKBACK_DAYS).round(2)

        out.to_csv(cache, index=False)
        log(f"Saved → {cache}")

    # ── Summary ───────────────────────────────────────────────────────────────
    domain_ex = out[out["exempt_domain"].notna()]
    log("═" * 60, "STEP")
    log(f"Total rows:                        {len(out):,}")
    log(f"Domain exemption rows:             {len(domain_ex):,}")
    log(f"  Combo spend > 0:                 {(domain_ex['combo_spend_7d'] > 0).sum():,}")
    log(f"  Combo spend = 0 (no cat match):  {(domain_ex['combo_spend_7d'] == 0).sum():,}")
    log(f"  Combo spend = null (no IAB map): {domain_ex['combo_spend_7d'].isna().sum():,}")

    top = (
        domain_ex[domain_ex["combo_spend_7d"] > 0]
        .sort_values("combo_spend_7d", ascending=False)
        .head(10)
    )
    if len(top) > 0:
        log("Top 10 combos by category-filtered 7d spend:", "STEP")
        for _, r in top.iterrows():
            net = f"${r['domain_network_spend_7d']:>9,.0f}" if pd.notna(r.get("domain_network_spend_7d")) else "        n/a"
            log(f"  {r['app_name'][:26]:<26} × {r['exempt_domain']:<26} [{r['adv_cat_id']}]  combo ${r['combo_spend_7d']:>8,.0f}  network {net}")

    log(f"Output: {OUTPUT_DIR}/p4_whitelist_audit.csv", "STEP")
    log("═" * 60, "STEP")
