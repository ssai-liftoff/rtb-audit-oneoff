"""
Fraud Banking Audit — Part 2: Run Banking Field Matching

Loads the Tipalti banking CSV and the IVT account list from Part 1.
Identifies IVT accounts in the banking data, builds their banking profiles,
then runs 8 matching passes against every non-IVT account.

Matching passes:
  1. account_number   — exact match on bank account number
  2. iban             — exact match on IBAN
  3. paypal_email     — exact match on PayPal email (case-insensitive)
  4. email            — exact match on account email (case-insensitive)
  5. address          — street1 + city + zip + country all present and matching (normalised);
                        street2 and state are optional and not used in matching
  6. identity         — first name + last name + DOB + country of birth all matching
  7. email_domain     — non-generic email domain shared with an IVT account
  8. typo_email       — email domain is a close typo variant of a major provider

For each match, one row is generated per (new_fraud_pub_id, matched_ivt_pub_id)
pair. If the same pair matches on multiple criteria, they are consolidated into
one row with a comma-separated list of match types and entities.

Inputs:
  - output/fraud_banking_audit/p1_ivt_accounts.csv
  - fraud_banking_audit/data/pub_banking_data.csv

Outputs:
  - output/fraud_banking_audit/p2_matches.csv
  - output/fraud_banking_audit/p2_domain_review.csv  (non-generic IVT domains for reference)
"""

import os
import re
import csv
import pandas as pd
from collections import defaultdict
from difflib import SequenceMatcher
from datetime import datetime

OUTPUT_DIR = "output/fraud_banking_audit"
BANKING_CSV = "fraud_banking_audit/data/pub_banking_data.csv"
IVT_ACCOUNTS_CSV = f"{OUTPUT_DIR}/p1_ivt_accounts.csv"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


# ── Generic email domain blocklist ────────────────────────────────────────────
# These are excluded from email domain clustering (too common to be meaningful).

GENERIC_DOMAINS = {
    # Google
    "gmail.com", "googlemail.com",
    # Microsoft
    "outlook.com", "outlook.co.uk", "outlook.com.au", "outlook.com.vn",
    "outlook.com.tr", "outlook.be", "outlook.fr", "outlook.de", "outlook.it",
    "outlook.sg", "outlook.jp", "outlook.kr", "outlook.sa", "outlook.ph",
    "outlook.co.th", "outlook.nl", "outlook.com.mx", "outlook.com.hk",
    "hotmail.com", "hotmail.co.uk", "hotmail.fr", "hotmail.de", "hotmail.it",
    "hotmail.com.tr", "hotmail.co.jp", "hotmail.gr", "hotmail.nl", "hotmail.es",
    "hotmail.com.tw", "hotmail.com.br", "hotmail.com.ar", "hotmail.com.mx",
    "live.com", "live.co.uk", "live.fr", "live.cn", "live.it", "live.ru",
    "live.nl", "live.com.au", "live.com.mx", "live.com.ar", "live.com.pt",
    "msn.com", "windowslive.com",
    # Yahoo
    "yahoo.com", "yahoo.co.uk", "yahoo.co.jp", "yahoo.com.vn", "yahoo.ca",
    "yahoo.it", "yahoo.no", "yahoo.co.id", "yahoo.in", "yahoo.com.br",
    "yahoo.com.ph", "yahoo.fr", "yahoo.co.in", "yahoo.com.tr", "yahoo.com.au",
    "yahoo.com.hk", "yahoo.com.sg", "yahoo.com.tw", "yahoo.com.ar",
    "yahoo.com.mx", "yahoo.com.co", "yahoo.de", "yahoo.es", "yahoo.com.ve",
    "ymail.com", "rocketmail.com",
    # Apple
    "icloud.com", "me.com", "mac.com",
    # AOL
    "aol.com",
    # ProtonMail / Tuta
    "protonmail.com", "proton.me", "pm.me",
    "tutanota.com", "tuta.io", "tutamail.com", "keemail.me",
    # GMX
    "gmx.com", "gmx.net", "gmx.de", "gmx.at", "gmx.us", "gmx.hk",
    "gmx.co.uk", "gmx.fr", "gmx.es", "gmx.ch",
    # Chinese providers
    "qq.com", "vip.qq.com", "163.com", "126.com", "yeah.net", "139.com",
    "vip.163.com", "sina.com", "sina.cn", "sohu.com", "foxmail.com",
    "tom.com", "21cn.com", "189.cn", "aliyun.com", "2980.com",
    # Russian providers
    "mail.ru", "bk.ru", "inbox.ru", "list.ru", "corp.mail.ru",
    "yandex.com", "yandex.ru", "yandex.com.tr", "ya.ru", "rambler.ru",
    # Korean providers
    "naver.com", "hanmail.net", "daum.net", "nate.com",
    # Other major free/ISP email
    "web.de", "t-online.de", "free.fr", "orange.fr", "laposte.net",
    "btinternet.com", "btopenworld.com", "talktalk.net", "virginmedia.com",
    "att.net", "bigpond.com", "comcast.net", "verizon.net",
    "mail.com", "email.cz", "email.com", "juno.com", "inbox.lv", "ukr.net",
    "op.pl", "rediffmail.com", "mail2world.com", "linuxmail.org",
    "mailfence.com", "zoho.com", "zohomail.com", "zohomail.eu",
    # Disposable / temp mail
    "mailinator.com", "yopmail.com", "temp-mail.org", "getnada.com",
    "guerrillamail.com", "throwaway.email", "sharklasers.com",
    "grr.la", "spam4.me", "10minutemail.com", "tempmail.com",
    "dispostable.com", "mailnull.com", "pokemail.net", "spam.la",
    "discard.email", "getairmail.com", "maildrop.cc",
}

# Major providers used for typo detection
MAJOR_PROVIDERS_FOR_TYPO = [
    "gmail.com", "hotmail.com", "yahoo.com", "outlook.com",
    "icloud.com", "protonmail.com", "aol.com",
]

TYPO_SIMILARITY_THRESHOLD = 0.95
ENTITY_VALUE_SEP = ", "


def format_address_entity(row):
    parts = [row["street1"], row["city"], row["zip"], row["addr_country"]]
    inner = ", ".join(p for p in parts if p)
    return f"{{{inner}}}" if inner else ""


def format_identity_entity(row):
    name = f"{row['first_name']} {row['last_name']}".strip()
    return f"{{{name}, DOB: {row['dob']}, COB: {row['country_of_birth']}}}"


def wrap_entity_value(value):
    value = str(value).strip()
    if value.startswith("{") and value.endswith("}"):
        return value
    return f"{{{value}}}"


def join_entity_values(entities):
    """Join matched values. Multiple values are wrapped in {} and separated by commas."""
    cleaned = [str(e).strip() for e in entities if str(e).strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return ENTITY_VALUE_SEP.join(wrap_entity_value(e) for e in cleaned)

_PLACEHOLDER_VALUES = {"no_country", "nocountry", "nopm", "n/a", "na", "none", "null", "unknown"}

def normalise(s):
    """Lowercase, strip punctuation, collapse whitespace. Returns '' for placeholder values."""
    s = str(s).lower().strip()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return "" if s in _PLACEHOLDER_VALUES else s


def get_email_domain(email):
    email = str(email).strip().lower()
    if "@" in email:
        return email.split("@")[-1].strip()
    return ""


def is_typo_domain(domain):
    """Return True if domain is a close fuzzy variant of a major provider (but not the provider itself)."""
    if not domain or domain in GENERIC_DOMAINS:
        return False
    for provider in MAJOR_PROVIDERS_FOR_TYPO:
        if domain == provider:
            return False
        ratio = SequenceMatcher(None, domain, provider).ratio()
        if ratio >= TYPO_SIMILARITY_THRESHOLD:
            return True
    return False


def safe(row, idx):
    """Safely get a value from a row by index, returning empty string if missing."""
    return row[idx].strip() if idx < len(row) and row[idx].strip() else ""


# ── Load banking CSV ──────────────────────────────────────────────────────────

BANKING_COLUMN_ALIASES = {
    "pub_id":           ["Id at payer", "pub id"],
    "email":            ["Email"],
    "company_name":     ["Company name"],
    "first_name":       ["First name"],
    "last_name":        ["Last name"],
    "street1":          ["Bene street1"],
    "city":             ["Bene city"],
    "zip":              ["Bene zip"],
    "addr_country":     ["Bene address country"],
    "paypal_email":     ["Pay pal email"],
    "iban":             ["IBAN"],
    "account_number":   ["Account number"],
    "dob":              ["Date of birth"],
    "country_of_birth": ["Country of birth"],
}


def banking_column_indices(header):
    """Resolve required banking columns by header name (supports old and new Tipalti exports)."""
    indices = {}
    missing = []
    for field, aliases in BANKING_COLUMN_ALIASES.items():
        idx = next((header.index(name) for name in aliases if name in header), None)
        if idx is None:
            missing.append("/".join(aliases))
        else:
            indices[field] = idx
    if missing:
        raise ValueError(f"Banking CSV missing required columns: {', '.join(missing)}")
    return indices


def load_banking_csv(path):
    """Load the Tipalti banking CSV using header-based column mapping."""
    records = []
    with open(path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        cols = banking_column_indices(header)
        for row in reader:
            records.append({
                "pub_id":           safe(row, cols["pub_id"]),
                "email":            safe(row, cols["email"]),
                "company_name":     safe(row, cols["company_name"]),
                "first_name":       safe(row, cols["first_name"]),
                "last_name":        safe(row, cols["last_name"]),
                "street1":          safe(row, cols["street1"]),
                "city":             safe(row, cols["city"]),
                "zip":              safe(row, cols["zip"]),
                "addr_country":     safe(row, cols["addr_country"]),
                "paypal_email":     safe(row, cols["paypal_email"]),
                "iban":             safe(row, cols["iban"]).upper(),
                "account_number":   safe(row, cols["account_number"]),
                "dob":              safe(row, cols["dob"]),
                "country_of_birth": safe(row, cols["country_of_birth"]),
            })
    df = pd.DataFrame(records)
    df = df[df["pub_id"] != ""].copy()
    log(f"Banking CSV loaded: {len(df):,} rows")
    return df


# ── Build IVT profile lookup maps ─────────────────────────────────────────────

def build_ivt_maps(ivt_pub_ids, banking_df):
    """
    For each IVT account present in the banking CSV, build lookup dicts
    keyed by the matching field value → list of (pub_id, pub_name) tuples.
    """
    ivt_set = set(str(p).strip() for p in ivt_pub_ids)
    ivt_rows = banking_df[banking_df["pub_id"].isin(ivt_set)].copy()
    log(f"IVT accounts found in banking CSV: {len(ivt_rows):,} / {len(ivt_set):,} total IVT accounts")

    maps = {
        "account_number": defaultdict(list),
        "iban":           defaultdict(list),
        "paypal_email":   defaultdict(list),
        "email":          defaultdict(list),
        "address":        defaultdict(list),
        "identity":       defaultdict(list),
        "email_domain":   defaultdict(list),
    }

    for _, row in ivt_rows.iterrows():
        pid  = row["pub_id"]
        name = row["company_name"] or f"{row['first_name']} {row['last_name']}".strip()

        # 1. Account number
        if row["account_number"]:
            maps["account_number"][row["account_number"]].append((pid, name))

        # 2. IBAN
        if row["iban"]:
            maps["iban"][row["iban"]].append((pid, name))

        # 3. PayPal email
        if row["paypal_email"]:
            maps["paypal_email"][row["paypal_email"].lower()].append((pid, name))

        # 4. Email
        if row["email"]:
            maps["email"][row["email"].lower()].append((pid, name))

        # 5. Address (street1 + city + zip + country all required; street2 and state ignored)
        s1 = normalise(row["street1"])
        ci = normalise(row["city"])
        zp = normalise(row["zip"])
        co = normalise(row["addr_country"])
        if s1 and ci and zp and co:
            maps["address"][(s1, ci, zp, co)].append((pid, name))

        # 6. Identity (all four fields required)
        fn = normalise(row["first_name"])
        ln = normalise(row["last_name"])
        dob = normalise(row["dob"])
        cob = normalise(row["country_of_birth"])
        if fn and ln and dob and cob:
            maps["identity"][(fn, ln, dob, cob)].append((pid, name))

        # 7. Email domain (exclude generic providers)
        domain = get_email_domain(row["email"])
        if domain and domain not in GENERIC_DOMAINS and not is_typo_domain(domain):
            maps["email_domain"][domain].append((pid, name))

    log(f"IVT banking profile built:")
    log(f"  Account numbers: {len(maps['account_number'])}")
    log(f"  IBANs:           {len(maps['iban'])}")
    log(f"  PayPal emails:   {len(maps['paypal_email'])}")
    log(f"  Emails:          {len(maps['email'])}")
    log(f"  Addresses:       {len(maps['address'])}")
    log(f"  Identities:      {len(maps['identity'])}")
    log(f"  Email domains:   {len(maps['email_domain'])}")

    return maps, ivt_rows


# ── Run matching passes ───────────────────────────────────────────────────────

def run_matching(banking_df, ivt_pub_ids, ivt_accounts_df, maps):
    """
    For each non-IVT account, run all 8 matching passes.
    Returns a list of match dicts.
    """
    ivt_set = set(str(p).strip() for p in ivt_pub_ids)
    non_ivt_df = banking_df[~banking_df["pub_id"].isin(ivt_set)].copy()
    log(f"Non-IVT accounts to check: {len(non_ivt_df):,}", "STEP")

    # Build IVT name lookup from Looker data (more reliable than company_name in CSV)
    ivt_name_lookup = dict(zip(
        ivt_accounts_df["pub_id"].astype(str),
        ivt_accounts_df["pub_name"].astype(str)
    ))

    # (new_pub_id, ivt_pub_id) → {match_types: [...], entities: [...]}
    hits = defaultdict(lambda: {"match_types": [], "entities": []})

    for _, row in non_ivt_df.iterrows():
        pid = row["pub_id"]

        # ── 1. Account number ────────────────────────────────────────────────
        if row["account_number"]:
            for (ivt_id, ivt_name) in maps["account_number"].get(row["account_number"], []):
                k = (pid, ivt_id)
                if "account_number" not in hits[k]["match_types"]:
                    hits[k]["match_types"].append("account_number")
                    hits[k]["entities"].append(row["account_number"])
                    hits[k]["ivt_name"] = ivt_name_lookup.get(ivt_id, ivt_name)

        # ── 2. IBAN ──────────────────────────────────────────────────────────
        if row["iban"]:
            for (ivt_id, ivt_name) in maps["iban"].get(row["iban"], []):
                k = (pid, ivt_id)
                if "iban" not in hits[k]["match_types"]:
                    hits[k]["match_types"].append("iban")
                    hits[k]["entities"].append(row["iban"])
                    hits[k].setdefault("ivt_name", ivt_name_lookup.get(ivt_id, ivt_name))

        # ── 3. PayPal email ──────────────────────────────────────────────────
        if row["paypal_email"]:
            for (ivt_id, ivt_name) in maps["paypal_email"].get(row["paypal_email"].lower(), []):
                k = (pid, ivt_id)
                if "paypal_email" not in hits[k]["match_types"]:
                    hits[k]["match_types"].append("paypal_email")
                    hits[k]["entities"].append(row["paypal_email"])
                    hits[k].setdefault("ivt_name", ivt_name_lookup.get(ivt_id, ivt_name))

        # ── 4. Email ─────────────────────────────────────────────────────────
        if row["email"]:
            for (ivt_id, ivt_name) in maps["email"].get(row["email"].lower(), []):
                k = (pid, ivt_id)
                if "email" not in hits[k]["match_types"]:
                    hits[k]["match_types"].append("email")
                    hits[k]["entities"].append(row["email"])
                    hits[k].setdefault("ivt_name", ivt_name_lookup.get(ivt_id, ivt_name))

        # ── 5. Address (street1 + city + zip + country all required; street2/state ignored) ──
        s1 = normalise(row["street1"])
        ci = normalise(row["city"])
        zp = normalise(row["zip"])
        co = normalise(row["addr_country"])
        if s1 and ci and zp and co:
            addr_key = (s1, ci, zp, co)
            for (ivt_id, ivt_name) in maps["address"].get(addr_key, []):
                k = (pid, ivt_id)
                if "address" not in hits[k]["match_types"]:
                    hits[k]["match_types"].append("address")
                    hits[k]["entities"].append(format_address_entity(row))
                    hits[k].setdefault("ivt_name", ivt_name_lookup.get(ivt_id, ivt_name))

        # ── 6. Identity (first + last + DOB + COB, all required) ─────────────
        fn = normalise(row["first_name"])
        ln = normalise(row["last_name"])
        dob = normalise(row["dob"])
        cob = normalise(row["country_of_birth"])
        if fn and ln and dob and cob:
            id_key = (fn, ln, dob, cob)
            for (ivt_id, ivt_name) in maps["identity"].get(id_key, []):
                k = (pid, ivt_id)
                if "identity" not in hits[k]["match_types"]:
                    hits[k]["match_types"].append("identity")
                    hits[k]["entities"].append(format_identity_entity(row))
                    hits[k].setdefault("ivt_name", ivt_name_lookup.get(ivt_id, ivt_name))

        # ── 7. Email domain (non-generic) ────────────────────────────────────
        domain = get_email_domain(row["email"])
        if domain and domain not in GENERIC_DOMAINS and not is_typo_domain(domain):
            for (ivt_id, ivt_name) in maps["email_domain"].get(domain, []):
                k = (pid, ivt_id)
                if "email_domain" not in hits[k]["match_types"]:
                    hits[k]["match_types"].append("email_domain")
                    hits[k]["entities"].append(domain)
                    hits[k].setdefault("ivt_name", ivt_name_lookup.get(ivt_id, ivt_name))

        # ── 8. Typo / fake email domain (standalone, no IVT cross-match) ─────
        if domain and is_typo_domain(domain):
            k = (pid, "N/A")
            if "typo_email" not in hits[k]["match_types"]:
                hits[k]["match_types"].append("typo_email")
                hits[k]["entities"].append(row["email"])
                hits[k].setdefault("ivt_name", "N/A")

    return hits, non_ivt_df


# ── Save domain review list ───────────────────────────────────────────────────

def save_domain_review(maps):
    """Export the non-generic IVT email domains used in clustering, for reference."""
    cache = f"{OUTPUT_DIR}/p2_domain_review.csv"
    rows = []
    for domain, ivt_pubs in maps["email_domain"].items():
        rows.append({
            "domain": domain,
            "ivt_account_count": len(ivt_pubs),
            "ivt_pub_ids": " | ".join(p[0] for p in ivt_pubs),
            "ivt_pub_names": " | ".join(p[1] for p in ivt_pubs),
        })
    df = pd.DataFrame(rows).sort_values("ivt_account_count", ascending=False)
    df.to_csv(cache, index=False)
    log(f"Domain review list saved → {cache} ({len(df)} unique fraud domains)")
    return df


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log("=" * 60, "STEP")
    log("FRAUD BANKING AUDIT — PART 2: RUN MATCHING", "STEP")
    log("=" * 60, "STEP")

    if not os.path.exists(IVT_ACCOUNTS_CSV):
        raise FileNotFoundError(f"IVT accounts not found at {IVT_ACCOUNTS_CSV}. Run p1 first.")

    ivt_accounts_df = pd.read_csv(IVT_ACCOUNTS_CSV, dtype=str).fillna("")
    log(f"Loaded {len(ivt_accounts_df):,} IVT accounts from Part 1")

    banking_df = load_banking_csv(BANKING_CSV)

    log("Building IVT banking profiles...", "STEP")
    maps, ivt_rows = build_ivt_maps(ivt_accounts_df["pub_id"].tolist(), banking_df)

    save_domain_review(maps)

    log("Running matching passes...", "STEP")
    hits, non_ivt_df = run_matching(banking_df, ivt_accounts_df["pub_id"].tolist(), ivt_accounts_df, maps)

    log(f"Total match pairs found: {len(hits):,}")

    # Build name lookup from banking CSV for new fraud accounts
    banking_name_lookup = {}
    for _, row in non_ivt_df.iterrows():
        name = row["company_name"] or f"{row['first_name']} {row['last_name']}".strip()
        banking_name_lookup[row["pub_id"]] = name

    # Assemble output rows
    output_rows = []
    for (new_pub_id, ivt_pub_id), info in hits.items():
        output_rows.append({
            "new_fraud_pub_id":        new_pub_id,
            "matched_fraud_pub_id":    ivt_pub_id,
            "matched_fraud_pub_name":  info.get("ivt_name", ""),
            "matched_on":              ", ".join(info["match_types"]),
            "matched_fraud_entity":    join_entity_values(info["entities"]),
        })

    output_df = pd.DataFrame(output_rows).sort_values(
        ["new_fraud_pub_id", "matched_fraud_pub_id"]
    )

    out_path = f"{OUTPUT_DIR}/p2_matches.csv"
    output_df.to_csv(out_path, index=False)
    log(f"Matches saved → {out_path}")

    # Summary by match type
    log("=" * 60, "STEP")
    log("PART 2 COMPLETE", "STEP")
    from collections import Counter
    type_counts = Counter()
    for info in hits.values():
        for mt in info["match_types"]:
            type_counts[mt] += 1
    for mt, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        log(f"  {mt:<20} {cnt:>5} matches", "STEP")
    log(f"  {'─'*30}", "STEP")
    unique_new = output_df["new_fraud_pub_id"].nunique()
    log(f"  Unique new fraud accounts: {unique_new}", "STEP")
    log("Next: run p3_fetch_spend.py", "STEP")
    log("=" * 60, "STEP")
