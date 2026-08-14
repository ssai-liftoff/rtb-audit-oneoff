"""Advertiser entity mappings for Kalshi / Polymarket parity audit."""

ADVERTISERS = {
    "kalshi": {
        "domain": "kalshi.com",
        "market_ids": ["1632713844", "com.kalshi.mobile"],
        "customer_ids": {"3043"},
        "advertiser_app_ids": {"9100", "9201"},
        "campaign_ids": {
            "71002", "54302", "76255", "50802", "53715", "75036", "48168",
            "76975", "47728", "60865", "76974", "52599", "77771", "48310",
            "78304", "76506", "76972", "77429", "76973", "48107",
        },
    },
    "polymarket": {
        "domain": "polymarket.com",
        "market_ids": ["6648798962"],
        "customer_ids": {"3092"},
        "advertiser_app_ids": {"9858"},
        "campaign_ids": {"71757", "76509", "77946", "70140"},
    },
}

# Alternate domain strings sometimes seen in publisher blocklists / spend reports.
DOMAIN_ALIASES = {
    "kalshi.com": {"kalshi.com"},
    "polymarket.com": {"polymarket.com", "polymarket.us"},
}


def normalize_id(val):
    """Normalize Looker entity IDs (handles 3043.0 → 3043, empty/- → '')."""
    if val is None:
        return ""
    s = str(val).strip()
    if s in ("", "-", "nan", "None", "NULL"):
        return ""
    if s.endswith(".0"):
        try:
            return str(int(float(s)))
        except ValueError:
            pass
    return s
