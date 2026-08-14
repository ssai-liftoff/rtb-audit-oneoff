"""
FIFA Blocklist Audit — Part 4: Build Final Audit Output

Cross-references publisher blocklists (from p3) against FIFA advertiser profiles
(from p2) to identify blocked combinations.

Block detection logic:
  - Domain block:   adomain is in publisher's adDomainBlacklist
  - Category block: adomain's IAB codes map to a V1-x/V2-x code that is in the
                    publisher's adCatBlocklist (via IAB_TO_INTERNAL mapping below)

For app-level category blocks, checks blocklistExemptions:
  - If an exemption exists where advCatId matches a blocking code AND
    (domain matches the adomain OR domain is blank), the block is flagged as
    "category_exempted" rather than a hard block.

Note: Exemptions can be scoped by rtbAccountId and country — an "exempted" row
means SOME spend can flow through, but not necessarily all. The output includes
exemption details so you can assess scope.

Output columns:
  level, account_id, account_name, app_id, app_name, am_user_name, am_user_region,
  publisher_daily_spend, adomain, adomain_daily_spend, adomain_iab_codes,
  adomain_internal_codes, block_type, blocked_domain_entries, blocked_cat_codes,
  has_exemption, exemption_details

Inputs:  output/fifa_blocklist_audit/p1_supply_accounts.csv
         output/fifa_blocklist_audit/p1_supply_apps.csv
         output/fifa_blocklist_audit/p2_advertiser_profiles.csv
         output/fifa_blocklist_audit/p3_account_blocklists.csv
         output/fifa_blocklist_audit/p3_app_blocklists.csv
Output:  output/fifa_blocklist_audit/p4_fifa_blocklist_audit.csv
"""

import os
import re
import json
import pandas as pd
from datetime import datetime

IAB_CODE_RE = re.compile(r"^IAB\d")

OUTPUT_DIR = "output/fifa_blocklist_audit"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


# ── Internal block code names ──────────────────────────────────────────────
INTERNAL_CAT_NAMES = {
    "V1-1":  "Alcohol",
    "V1-2":  "Associated with violence",
    "V1-3":  "Credit & debit cards",
    "V1-4":  "Relationships",
    "V1-5":  "Drugs & supplements",
    "V1-6":  "Gambling",
    "V1-7":  "Government & politics",
    "V1-8":  "n/a",
    "V1-9":  "Mature apps",
    "V1-10": "References to firearms",
    "V1-11": "Religion & spiritual",
    "V1-12": "Reproductive health",
    "V1-13": "Sexually suggestive",
    "V1-14": "Illegal content",
    "V2-1":  "Health & Fitness",
    "V2-2":  "News",
    "V2-3":  "Shooting",
    "V2-4":  "Teen",
    "V2-5":  "Uncategorized",
    "V2-6":  "Sports",
    "V2-7":  "Food & Drink",
    "V2-8":  "Family & Parenting",
    "V2-9":  "Contests & Freebies",
    "V2-10": "Personal Finance",
    "V2-11": "Society",
    "V2-12": "Veterinary Medicine",
    "V2-13": "Government & U.S. Military",
    "V2-14": "Incentivized",
    "V2-15": "Non-standard content",
    "V2-16": "Dating",
    "V2-17": "Senior people",
    "V2-18": "Herbs and nature",
    "V2-19": "Mysteries and the Unexplained",
    "V2-20": "Video & Computer Games",
    "V2-21": "Weight loss",
}

# ── IAB code → internal block code(s) reverse mapping ─────────────────────
# Source: category mapping table shared by user.
IAB_TO_INTERNAL = {
    # V1-1 Alcohol
    "IAB8-5":  ["V1-1"], "IAB8-18": ["V1-1", "V1-9"],
    # V1-2 Associated with violence
    "IAB25-2": ["V1-2"],
    # V1-3 Credit & debit cards
    "IAB13-2": ["V1-3"],
    # V1-4 Relationships
    "IAB14-2": ["V1-4"], "IAB14-3": ["V1-4", "V1-9"], "IAB14-4": ["V1-4"],
    # V1-5 Drugs & supplements
    "IAB7-3":  ["V1-5"], "IAB7-5":  ["V1-5"], "IAB7-28": ["V1-5"],
    "IAB7-29": ["V1-5"], "IAB7-30": ["V1-5", "V1-12"], "IAB7-41": ["V1-5"],
    "IAB7-42": ["V1-5"],
    # V1-6 Gambling
    "IAB9-7":  ["V1-6"],
    # V1-7 Government & politics
    "IAB11":   ["V1-7"],
    # V1-9 Mature apps
    "IAB7-39": ["V1-9", "V1-13"], "IAB18-2": ["V1-9"],
    "IAB25-3": ["V1-9", "V1-13"], "IAB9-9":  ["V1-9"],
    # V1-10 References to firearms
    "IAB25-1": ["V1-10"],
    # V1-11 Religion & spiritual
    "IAB23":   ["V1-11"], "IAB14-8": ["V1-11"],
    # V1-12 Reproductive health (IAB7-30 already covered above)
    # V1-13 Sexually suggestive (covered above via IAB7-39, IAB25-3)
    "IAB25-4": ["V1-13"], "IAB25-5": ["V1-13"], "IAB25-6": ["V1-13"],
    # V1-14 Illegal content
    "IAB26":   ["V1-14"],
    # V2-1 Health & Fitness
    "IAB7-2":  ["V2-1"], "IAB7-6":  ["V2-1"], "IAB7-7":  ["V2-1"],
    "IAB7-8":  ["V2-1"], "IAB7-9":  ["V2-1"], "IAB7-10": ["V2-1"],
    "IAB7-11": ["V2-1"], "IAB7-12": ["V2-1"], "IAB7-13": ["V2-1"],
    "IAB7-14": ["V2-1"], "IAB7-15": ["V2-1"], "IAB7-16": ["V2-1"],
    "IAB7-18": ["V2-1"], "IAB7-19": ["V2-1"], "IAB7-20": ["V2-1"],
    "IAB7-21": ["V2-1"], "IAB7-22": ["V2-1"], "IAB7-23": ["V2-1"],
    "IAB7-24": ["V2-1"], "IAB7-27": ["V2-1"], "IAB7-33": ["V2-1"],
    "IAB7-34": ["V2-1"], "IAB7-35": ["V2-1"], "IAB7-43": ["V2-1"],
    "IAB20-24":["V2-1"],
    # V2-2 News
    "IAB12":   ["V2-2"], "IAB1-2":  ["V2-2"],
    # V2-3 Shooting
    "IAB17-18":["V2-3"],
    # V2-4 Teen
    "IAB14-6": ["V2-4"],
    # V2-5 Uncategorized
    "IAB24":   ["V2-5"],
    # V2-6 Sports
    "IAB17-4": ["V2-6"], "IAB17-5": ["V2-6"], "IAB17-7": ["V2-6"],
    "IAB17-14":["V2-6"], "IAB17-16":["V2-6"], "IAB17-20":["V2-6"],
    "IAB17-24":["V2-6"], "IAB17-28":["V2-6"], "IAB17-29":["V2-6"],
    # V2-7 Food & Drink
    "IAB8-6":  ["V2-7"], "IAB8-9":  ["V2-7"],
    # V2-8 Family & Parenting
    "IAB6-1":  ["V2-8"], "IAB6-6":  ["V2-8"], "IAB6-7":  ["V2-8"],
    # V2-9 Contests & Freebies
    "IAB22-1": ["V2-9"],
    # V2-10 Personal Finance
    "IAB13":   ["V2-10"],
    # V2-11 Society
    "IAB14":   ["V2-11"],
    # V2-12 Veterinary Medicine
    "IAB16-7": ["V2-12"],
    # V2-13 Government & U.S. Military
    "IAB3-7":  ["V2-13"], "IAB4-10": ["V2-13"],
    # V2-14 Incentivized
    "IAB25-7": ["V2-14"],
    # V2-15 Non-standard content
    "IAB25":   ["V2-15"],
    # V2-16 Dating
    "IAB14-1": ["V2-16"],
    # V2-17 Senior people
    "IAB7-38": ["V2-17"], "IAB14-5": ["V2-17"],
    # V2-18 Herbs and nature
    "IAB7-25": ["V2-18"], "IAB7-26": ["V2-18"],
    # V2-19 Mysteries and the Unexplained
    "IAB15-5": ["V2-19"],
    # V2-20 Video & Computer Games
    "IAB9-30": ["V2-20"],
    # V2-21 Weight loss
    "IAB7-44": ["V2-21"],
}


# Reverse mapping: internal code → set of IAB codes that map to it
INTERNAL_TO_IAB: dict = {}
for _iab, _vcodes in IAB_TO_INTERNAL.items():
    for _v in _vcodes:
        INTERNAL_TO_IAB.setdefault(_v, set()).add(_iab)


def iab_to_internal_codes(iab_codes_str):
    """Convert comma-separated IAB codes to a sorted set of internal V1-x/V2-x codes."""
    if not iab_codes_str or pd.isna(iab_codes_str):
        return set()
    internal = set()
    for code in str(iab_codes_str).split(","):
        code = code.strip()
        if code in IAB_TO_INTERNAL:
            internal.update(IAB_TO_INTERNAL[code])
    return internal


def parse_list(val):
    """Parse a JSON list column that may be stored as a string."""
    if pd.isna(val) or val in ("", "[]"):
        return []
    try:
        return json.loads(val)
    except Exception:
        return []


def check_exemptions(exemptions, blocking_cat_codes, adomain):
    """
    Given a list of exemption dicts and the set of cat codes that triggered a
    category block, return exemptions that cover this adomain for any of those codes.

    An exemption covers this combination if:
      - advCatId is one of the blocking codes, AND
      - domain matches the adomain OR domain is blank (broad exemption)
    """
    relevant = []
    for ex in exemptions:
        cat_match    = ex.get("advCatId", "") in blocking_cat_codes
        domain_val   = (ex.get("domain") or "").strip().lower()
        domain_match = (domain_val == adomain.lower()) or (domain_val == "")
        if cat_match and domain_match:
            relevant.append(ex)
    return relevant


def check_publisher(domain_blocklist, cat_blocklist, exemptions,
                    adomain, adomain_internal_codes, is_app=False):
    """
    Returns a dict describing the block status for one publisher × adomain pair.

    block_type values:
      "domain"              — adomain explicitly in domain blocklist
      "category"            — category block with no exemption
      "domain+category"     — both domain and category block
      "category_exempted"   — category block exists but adomain has an exemption
                              (some spend may still flow, but not all)
      None                  — no block found
    """
    domain_bl  = {d.strip().lower() for d in domain_blocklist}
    cat_bl     = {c.strip() for c in cat_blocklist}

    adomain_lower = adomain.strip().lower()
    is_domain_blocked = adomain_lower in domain_bl

    # Category block: any of the adomain's mapped internal codes are in publisher's cat blocklist
    matching_cat_codes = adomain_internal_codes & cat_bl
    is_cat_blocked = len(matching_cat_codes) > 0

    if not is_domain_blocked and not is_cat_blocked:
        return None

    exempt_details = []
    is_exempted    = False
    if is_cat_blocked and is_app and exemptions:
        exempt_details = check_exemptions(exemptions, matching_cat_codes, adomain)
        is_exempted    = len(exempt_details) > 0

    # Determine block_type
    if is_domain_blocked and is_cat_blocked:
        bt = "domain+category"
        if is_exempted:
            bt = "domain+category_exempted"
    elif is_domain_blocked:
        bt = "domain"
    elif is_cat_blocked:
        bt = "category_exempted" if is_exempted else "category"

    return {
        "block_type":            bt,
        "is_domain_blocked":     is_domain_blocked,
        "is_category_blocked":   is_cat_blocked,
        "blocked_cat_codes":     ",".join(sorted(matching_cat_codes)) if is_cat_blocked else "",
        "has_exemption":         is_exempted,
        "exemption_details":     json.dumps(exempt_details) if exempt_details else "",
    }


if __name__ == "__main__":
    log("═" * 60, "STEP")
    log("FIFA BLOCKLIST AUDIT — PART 4: BUILD AUDIT", "STEP")
    log("═" * 60, "STEP")

    # ── Load all inputs ───────────────────────────────────────────────────
    for path in [
        f"{OUTPUT_DIR}/p1_supply_accounts.csv",
        f"{OUTPUT_DIR}/p1_supply_apps.csv",
        f"{OUTPUT_DIR}/p2_advertiser_profiles.csv",
        f"{OUTPUT_DIR}/p2_adomain_iab_spend.csv",
        f"{OUTPUT_DIR}/p3_account_blocklists.csv",
        f"{OUTPUT_DIR}/p3_app_blocklists.csv",
    ]:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing input: {path} — re-run the relevant part")

    accounts_df    = pd.read_csv(f"{OUTPUT_DIR}/p1_supply_accounts.csv")
    apps_df        = pd.read_csv(f"{OUTPUT_DIR}/p1_supply_apps.csv")
    advertiser_df  = pd.read_csv(f"{OUTPUT_DIR}/p2_advertiser_profiles.csv")
    iab_spend_df   = pd.read_csv(f"{OUTPUT_DIR}/p2_adomain_iab_spend.csv")
    acct_blocks_df = pd.read_csv(f"{OUTPUT_DIR}/p3_account_blocklists.csv")
    app_blocks_df  = pd.read_csv(f"{OUTPUT_DIR}/p3_app_blocklists.csv")

    log(f"Publisher accounts:    {len(accounts_df):,}")
    log(f"Publisher apps:        {len(apps_df):,}")
    log(f"FIFA adomains:         {len(advertiser_df):,}")

    # Build blocklist lookup dicts for fast access
    acct_blocks = {
        str(r["account_id"]): {
            "domain_bl":  parse_list(r.get("domain_blocklist")),
            "cat_bl":     parse_list(r.get("cat_blocklist")),
        }
        for _, r in acct_blocks_df.iterrows()
    }

    app_blocks = {
        str(r["app_id"]): {
            "domain_bl":  parse_list(r.get("domain_blocklist")),
            "cat_bl":     parse_list(r.get("cat_blocklist")),
            "exemptions": parse_list(r.get("exemptions")),
        }
        for _, r in app_blocks_df.iterrows()
    }

    # Total qualifying supply daily spend — denominator for uplift calculation
    total_supply_daily_spend = apps_df["daily_spend"].sum()
    log(f"Total qualifying supply daily spend: ${total_supply_daily_spend:,.0f}/day")

    # (adomain, iab_code) → daily_spend lookup for category-specific uplift
    iab_spend_lookup: dict = {}
    for _, r in iab_spend_df.iterrows():
        iab_spend_lookup[(str(r["adomain"]).lower(), str(r["iab_code"]).strip())] = float(r["daily_spend"] or 0)

    # Pre-compute internal codes for each adomain
    # Also clean up IAB codes: keep only properly formatted ones, deduplicate
    def clean_iab_codes(raw):
        if not raw or pd.isna(raw):
            return ""
        parts = [c.strip() for c in str(raw).split(",")]
        valid = sorted(set(c for c in parts if IAB_CODE_RE.match(c) and len(c) <= 10))
        return ",".join(valid)

    adomain_profiles = {}
    for _, row in advertiser_df.iterrows():
        clean_codes = clean_iab_codes(row.get("iab_codes", ""))
        adomain_profiles[str(row["adomain"]).lower()] = {
            "iab_codes":       clean_codes,
            "daily_spend":     float(row.get("daily_spend", 0) or 0),
            "internal_codes":  iab_to_internal_codes(clean_codes),
        }

    results = []

    def calc_uplift(block_type, adomain, profile, blocking_cat_codes_str, pub_spend):
        """
        Domain block  → full adomain daily spend (all traffic blocked).
        Category block → only the spend tagged under IAB codes that triggered the block.
        Domain+category → domain covers everything, use full adomain spend.
        """
        if total_supply_daily_spend <= 0 or pub_spend <= 0:
            return 0.0
        if block_type in ("domain", "domain+category", "domain+category_exempted"):
            blocked_spend = profile["daily_spend"]
        else:
            # Find IAB codes that map to the blocking V1-x codes
            blocking_vcodes = set(blocking_cat_codes_str.split(",")) if blocking_cat_codes_str else set()
            blocking_iab = set()
            for v in blocking_vcodes:
                blocking_iab.update(INTERNAL_TO_IAB.get(v, set()))
            blocked_spend = sum(
                iab_spend_lookup.get((adomain, iab), 0.0) for iab in blocking_iab
            )
        return round(blocked_spend / total_supply_daily_spend * pub_spend, 2)

    def format_blocked_cat_names(blocked_cat_codes_str):
        if not blocked_cat_codes_str:
            return ""
        return ", ".join(
            f"{c} ({INTERNAL_CAT_NAMES.get(c, '?')})"
            for c in blocked_cat_codes_str.split(",")
        )

    # ── Account-level checks ──────────────────────────────────────────────
    log("Checking account-level blocks...")
    for _, acct in accounts_df.iterrows():
        acct_id  = str(acct["account_id"])
        bl       = acct_blocks.get(acct_id)
        if bl is None:
            continue  # API fetch failed for this account

        for adomain, profile in adomain_profiles.items():
            hit = check_publisher(
                bl["domain_bl"], bl["cat_bl"], [],
                adomain, profile["internal_codes"], is_app=False
            )
            if hit is None:
                continue

            pub_spend = float(acct.get("daily_spend", 0) or 0)
            uplift = calc_uplift(hit["block_type"], adomain, profile, hit["blocked_cat_codes"], pub_spend)
            results.append({
                "level":                  "account",
                "account_id":             acct_id,
                "account_name":           acct.get("account_name", ""),
                "app_id":                 "",
                "app_name":               "",
                "am_user_name":           "",
                "am_user_region":         "",
                "publisher_daily_spend":  pub_spend,
                "adomain":                adomain,
                "adomain_daily_spend":    profile["daily_spend"],
                "estimated_uplift":       uplift,
                "adomain_iab_codes":      profile["iab_codes"],
                "adomain_internal_codes": ",".join(sorted(profile["internal_codes"])),
                "blocked_cat_names":      format_blocked_cat_names(hit["blocked_cat_codes"]),
                **hit,
            })

    log(f"  Account-level blocks found: {len(results):,}")

    # ── App-level checks ──────────────────────────────────────────────────
    log("Checking app-level blocks...")
    app_count_before = len(results)

    for _, app in apps_df.iterrows():
        app_id  = str(app["app_id"])
        bl      = app_blocks.get(app_id)
        if bl is None:
            continue

        for adomain, profile in adomain_profiles.items():
            hit = check_publisher(
                bl["domain_bl"], bl["cat_bl"], bl["exemptions"],
                adomain, profile["internal_codes"], is_app=True
            )
            if hit is None:
                continue

            pub_spend = float(app.get("daily_spend", 0) or 0)
            uplift = calc_uplift(hit["block_type"], adomain, profile, hit["blocked_cat_codes"], pub_spend)
            results.append({
                "level":                  "app",
                "account_id":             str(app.get("account_id", "")),
                "account_name":           app.get("account_name", ""),
                "app_id":                 app_id,
                "app_name":               app.get("app_name", ""),
                "am_user_name":           "",
                "am_user_region":         "",
                "publisher_daily_spend":  pub_spend,
                "adomain":                adomain,
                "adomain_daily_spend":    profile["daily_spend"],
                "estimated_uplift":       uplift,
                "adomain_iab_codes":      profile["iab_codes"],
                "adomain_internal_codes": ",".join(sorted(profile["internal_codes"])),
                "blocked_cat_names":      format_blocked_cat_names(hit["blocked_cat_codes"]),
                **hit,
            })

    log(f"  App-level blocks found: {len(results) - app_count_before:,}")

    # ── Save output ───────────────────────────────────────────────────────
    if not results:
        log("No blocks found — empty output", "WARN")
        final_df = pd.DataFrame()
    else:
        final_df = pd.DataFrame(results)
        final_df = final_df.sort_values(
            ["adomain", "publisher_daily_spend"],
            ascending=[True, False]
        ).reset_index(drop=True)

        # Rename columns to human-readable labels
        final_df = final_df.rename(columns={
            "level":                  "Level",
            "account_id":             "Account ID",
            "account_name":           "Account Name",
            "app_id":                 "App ID",
            "app_name":               "App Name",
            "am_user_name":           "AM Name",
            "am_user_region":         "AM Region",
            "publisher_daily_spend":  "Publisher Daily Spend ($)",
            "adomain":                "Adomain",
            "adomain_daily_spend":    "Adomain Daily Spend ($)",
            "estimated_uplift":       "Estimated Uplift ($)",
            "adomain_iab_codes":      "IAB Codes",
            "adomain_internal_codes": "Internal Cat Codes",
            "block_type":             "Block Type",
            "is_domain_blocked":      "Domain Blocked",
            "is_category_blocked":    "Category Blocked",
            "blocked_cat_codes":      "Blocked Cat Codes",
            "blocked_cat_names":      "Blocked Cat Names",
            "has_exemption":          "Has Exemption",
            "exemption_details":      "Exemption Details",
        })

    out_path = f"{OUTPUT_DIR}/p4_fifa_blocklist_audit.csv"
    final_df.to_csv(out_path, index=False)
    log(f"Saved → {out_path}")

    # ── Summary ───────────────────────────────────────────────────────────
    log("═" * 60, "STEP")
    log(f"Total blocked combinations: {len(final_df):,}")
    if len(final_df):
        bt_counts = final_df["Block Type"].value_counts()
        for bt, ct in bt_counts.items():
            log(f"  {bt:<30} {ct:,}")
        log("")
        log("Top blocked adomains by number of affected publishers:")
        top = (
            final_df.groupby("Adomain")
            .agg(publishers=("Account ID", "nunique"), blocks=("Block Type", "count"))
            .sort_values("publishers", ascending=False)
            .head(10)
        )
        for adomain, row in top.iterrows():
            log(f"  {adomain:<40} {row['publishers']:>4} publishers  {row['blocks']:>5} blocks")
        log("")
        log("Block types by level:")
        log(f"  Account-level: {(final_df['Level'] == 'account').sum():,}")
        log(f"  App-level:     {(final_df['Level'] == 'app').sum():,}")
    log("═" * 60, "STEP")
    log("PART 4 COMPLETE", "STEP")
    log("═" * 60, "STEP")
