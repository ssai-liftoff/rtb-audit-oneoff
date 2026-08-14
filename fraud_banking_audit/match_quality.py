"""Heuristics for flagging low-confidence address and email-domain fraud matches."""

import csv
import re
from collections import Counter
from difflib import SequenceMatcher

GENERIC_STREETS = {
    "hong kong", "hongkong", "hanoi", "singapore", "london", "dubai", "kowloon",
    "nha trang", "ho chi minh", "hcm", "hochiminh", "shanghai", "beijing",
    "mumbai", "delhi", "seoul", "taipei", "saigon", "vietnam", "china",
    "new york", "manchester", "birmingham", "toronto", "sydney", "melbourne",
}

PLACEHOLDER_TOKENS = {
    "na", "n/a", "none", "null", "unknown", "test", "xxx", "tbd", "nil",
    "no address", "noaddress", "-", ".", "0", "000", "same",
}

GENERIC_POSTAL_CODES = {
    "999077", "100000", "000000", "000", "00000", "99999", "12345", "11111", "0000",
}

GENERIC_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "outlook.com", "outlook.co.uk", "outlook.com.au",
    "outlook.com.vn", "outlook.com.tr", "outlook.be", "outlook.fr", "outlook.de",
    "outlook.it", "outlook.sg", "outlook.jp", "outlook.kr", "outlook.sa", "outlook.ph",
    "outlook.co.th", "outlook.nl", "outlook.com.mx", "outlook.com.hk", "hotmail.com",
    "hotmail.co.uk", "hotmail.fr", "hotmail.de", "hotmail.it", "hotmail.com.tr",
    "hotmail.co.jp", "hotmail.gr", "hotmail.nl", "hotmail.es", "hotmail.com.tw",
    "hotmail.com.br", "hotmail.com.ar", "hotmail.com.mx", "live.com", "live.co.uk",
    "live.fr", "live.cn", "live.it", "live.ru", "live.nl", "live.com.au",
    "live.com.mx", "live.com.ar", "live.com.pt", "msn.com", "windowslive.com",
    "yahoo.com", "yahoo.co.uk", "yahoo.co.jp", "yahoo.com.vn", "yahoo.ca", "yahoo.it",
    "yahoo.no", "yahoo.co.id", "yahoo.in", "yahoo.com.br", "yahoo.com.ph", "yahoo.fr",
    "yahoo.co.in", "yahoo.com.tr", "yahoo.com.au", "yahoo.com.hk", "yahoo.com.sg",
    "yahoo.com.tw", "yahoo.com.ar", "yahoo.com.mx", "yahoo.com.co", "yahoo.de",
    "yahoo.es", "yahoo.com.ve", "ymail.com", "rocketmail.com", "icloud.com", "me.com",
    "mac.com", "aol.com", "protonmail.com", "proton.me", "pm.me", "tutanota.com",
    "tuta.io", "tutamail.com", "keemail.me", "gmx.com", "gmx.net", "gmx.de", "gmx.at",
    "gmx.us", "gmx.hk", "gmx.co.uk", "gmx.fr", "gmx.es", "gmx.ch", "qq.com",
    "vip.qq.com", "163.com", "126.com", "yeah.net", "139.com", "vip.163.com",
    "sina.com", "sina.cn", "sohu.com", "foxmail.com", "tom.com", "21cn.com", "189.cn",
    "aliyun.com", "2980.com", "mail.ru", "bk.ru", "inbox.ru", "list.ru", "corp.mail.ru",
    "yandex.com", "yandex.ru", "yandex.com.tr", "ya.ru", "rambler.ru", "naver.com",
    "hanmail.net", "daum.net", "nate.com", "web.de", "t-online.de", "free.fr",
    "orange.fr", "laposte.net", "btinternet.com", "btopenworld.com", "talktalk.net",
    "virginmedia.com", "att.net", "bigpond.com", "comcast.net", "verizon.net",
    "mail.com", "email.cz", "email.com", "juno.com", "inbox.lv", "ukr.net", "op.pl",
    "rediffmail.com", "mail2world.com", "linuxmail.org", "mailfence.com", "zoho.com",
    "zohomail.com", "zohomail.eu", "mailinator.com", "yopmail.com", "temp-mail.org",
    "getnada.com", "guerrillamail.com", "throwaway.email", "sharklasers.com", "grr.la",
    "spam4.me", "10minutemail.com", "tempmail.com", "dispostable.com", "mailnull.com",
    "pokemail.net", "spam.la", "discard.email", "getairmail.com", "maildrop.cc",
}

MAJOR_EMAIL_PROVIDERS = [
    "gmail.com", "hotmail.com", "yahoo.com", "outlook.com",
    "icloud.com", "protonmail.com", "aol.com",
]

PUBLIC_ISP_DOMAIN_HINTS = (
    ".edu.pl", ".edu.vn", ".edu.hk", ".ac.uk", ".gov.", ".mil.",
)


def normalise(value):
    return re.sub(r"[^\w\s]", "", str(value).lower().strip())


def is_typo_email_domain(domain):
    if not domain or domain in GENERIC_EMAIL_DOMAINS:
        return False
    for provider in MAJOR_EMAIL_PROVIDERS:
        if domain == provider:
            return False
        if SequenceMatcher(None, domain, provider).ratio() >= 0.95:
            return True
    return False


def is_address_block(block):
    parts = [part.strip() for part in block.split(",")]
    if len(parts) < 4:
        return False
    country = parts[-1]
    return len(country) == 2 and country.isalpha()


def parse_braced_values(entity_value):
    return re.findall(r"\{([^}]+)\}", str(entity_value))


def parse_address_parts(block):
    parts = [part.strip() for part in block.split(",")]
    if len(parts) < 4:
        return None
    return {
        "street": ", ".join(parts[:-3]),
        "city": parts[-3],
        "zip": parts[-2],
        "country": parts[-1],
    }


def extract_email_domains(entity_value):
    domains = []
    blocks = parse_braced_values(entity_value)
    if blocks:
        for block in blocks:
            if "DOB:" in block or is_address_block(block):
                continue
            candidate = block.strip().lower()
            if "@" in candidate:
                candidate = candidate.split("@")[-1].strip()
            if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", candidate):
                domains.append(candidate)
    else:
        value = str(entity_value).strip().lower()
        if "@" in value:
            domains.append(value.split("@")[-1].strip())
        elif re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", value):
            domains.append(value)
    return list(dict.fromkeys(domains))


def build_banking_address_counts(banking_csv_path):
    with open(banking_csv_path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        cols = {
            "street1": header.index("Bene street1"),
            "city": header.index("Bene city"),
            "zip": header.index("Bene zip"),
            "addr_country": header.index("Bene address country"),
        }
        counts = Counter()
        for row in reader:
            key = address_key(
                row[cols["street1"]] if cols["street1"] < len(row) else "",
                row[cols["city"]] if cols["city"] < len(row) else "",
                row[cols["zip"]] if cols["zip"] < len(row) else "",
                row[cols["addr_country"]] if cols["addr_country"] < len(row) else "",
            )
            if key:
                counts[key] += 1
    return counts


def build_banking_email_domain_counts(banking_csv_path):
    with open(banking_csv_path, encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)
        email_idx = header.index("Email")
        counts = Counter()
        for row in reader:
            email = row[email_idx].strip().lower() if email_idx < len(row) else ""
            if "@" not in email:
                continue
            counts[email.split("@")[-1]] += 1
    return counts


def load_ivt_domain_counts(domain_review_csv):
    counts = {}
    with open(domain_review_csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            domain = str(row.get("domain", "")).strip().lower()
            if domain:
                counts[domain] = int(float(row.get("ivt_account_count", 0) or 0))
    return counts


def address_key(street, city, zipcode, country):
    parts = [normalise(street), normalise(city), normalise(zipcode), normalise(country)]
    if not all(parts):
        return None
    return tuple(parts)


def assess_address(street, city, zipcode, country, banking_count=1):
    reasons = []
    street_norm = normalise(street)
    city_norm = normalise(city)

    if street_norm in PLACEHOLDER_TOKENS or city_norm in PLACEHOLDER_TOKENS:
        reasons.append("placeholder token in address fields")

    if street_norm == city_norm:
        reasons.append("street equals city")

    if street_norm in GENERIC_STREETS:
        reasons.append("street is only a generic location name")

    if city_norm in GENERIC_STREETS and len(street_norm) <= len(city_norm) + 2:
        reasons.append("street looks like city/country placeholder")

    if zipcode.strip() in GENERIC_POSTAL_CODES and (
        street_norm == city_norm or street_norm in GENERIC_STREETS or len(street_norm) < 12
    ):
        reasons.append("common placeholder postal code with weak street")

    if len(street_norm) <= 3:
        reasons.append("street too short")

    if not re.search(r"\d", street) and len(street_norm) < 20 and (
        street_norm == city_norm or street_norm in GENERIC_STREETS
    ):
        reasons.append("no street number and generic location")

    if banking_count >= 10:
        reasons.append(f"mass-shared address ({banking_count} accounts in banking data)")
    elif banking_count >= 5:
        reasons.append(f"likely virtual office ({banking_count} accounts in banking data)")
    elif banking_count >= 3 and (
        street_norm == city_norm
        or street_norm in GENERIC_STREETS
        or zipcode.strip() in GENERIC_POSTAL_CODES
        or not re.search(r"\d", street)
    ):
        reasons.append(f"shared address ({banking_count} accounts in banking data)")

    return reasons


def assess_address_row(match_types, entity_value, banking_counts):
    if "address" not in str(match_types).lower():
        return False, ""

    all_reasons = []
    for block in parse_braced_values(entity_value):
        parts = parse_address_parts(block)
        if not parts:
            continue
        key = address_key(parts["street"], parts["city"], parts["zip"], parts["country"])
        count = banking_counts.get(key, 1) if key else 1
        all_reasons.extend(assess_address(
            parts["street"], parts["city"], parts["zip"], parts["country"], count
        ))

    deduped = list(dict.fromkeys(all_reasons))
    if not deduped:
        return False, ""

    strong = any(
        reason.startswith(("mass-shared", "likely virtual", "placeholder", "street equals city"))
        for reason in deduped
    )
    if strong or len(deduped) >= 2:
        return True, "; ".join(deduped)
    if len(deduped) == 1 and deduped[0].startswith("shared address"):
        return True, deduped[0]
    return True, "; ".join(deduped)


def assess_email_domain(domain, ivt_count=0, banking_count=0):
    reasons = []
    domain = str(domain).strip().lower()

    if domain in GENERIC_EMAIL_DOMAINS:
        reasons.append("generic free email provider")

    if is_typo_email_domain(domain):
        reasons.append("typo variant of major email provider")

    if not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", domain):
        reasons.append("malformed email domain")

    if ivt_count <= 1:
        reasons.append(f"only {ivt_count or 0} known IVT account on this domain")

    if ivt_count <= 1 and banking_count >= 5:
        reasons.append(f"domain used by {banking_count} accounts in banking data")

    if banking_count >= 150 and ivt_count <= 2:
        reasons.append(f"very common platform domain ({banking_count} accounts in banking data)")

    if any(domain.endswith(suffix) or suffix.strip(".") in domain for suffix in PUBLIC_ISP_DOMAIN_HINTS):
        if ivt_count <= 2 and banking_count >= 20:
            reasons.append("public or institutional domain pattern with wide usage")

    deduped = list(dict.fromkeys(reasons))
    if domain in GENERIC_EMAIL_DOMAINS or is_typo_email_domain(domain):
        return "LOW", deduped

    if ivt_count >= 5:
        return "HIGH", deduped

    if ivt_count >= 2 and banking_count < 20:
        return "HIGH", deduped

    if ivt_count <= 1 and banking_count >= 5:
        return "LOW", deduped

    if ivt_count <= 1:
        return "MEDIUM", deduped

    if deduped:
        return "MEDIUM", deduped

    return "HIGH", []


def assess_email_domain_row(match_types, entity_value, ivt_domain_counts, banking_domain_counts):
    if "email_domain" not in str(match_types).lower():
        return False, "", ""

    domains = extract_email_domains(entity_value)
    if not domains:
        return False, "", ""

    levels = []
    all_reasons = []
    for domain in domains:
        ivt_count = ivt_domain_counts.get(domain, 0)
        banking_count = banking_domain_counts.get(domain, 0)
        level, reasons = assess_email_domain(domain, ivt_count, banking_count)
        levels.append(level)
        all_reasons.extend(reasons)

    deduped = list(dict.fromkeys(all_reasons))
    if "LOW" in levels:
        final_level = "LOW"
    elif "MEDIUM" in levels:
        final_level = "MEDIUM"
    else:
        final_level = "HIGH"

    return final_level == "LOW", final_level, "; ".join(deduped)
