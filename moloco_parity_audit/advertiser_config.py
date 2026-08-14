"""Moloco RTB parity audit — advertiser identifiers and IAB codes."""

ADVERTISERS = {
    "kalshi": {
        "domain": "kalshi.com",
        "market_ids": ["1632713844", "com.kalshi.mobile"],
        "iab_code": "IAB13",
    },
    "polymarket": {
        "domain": "polymarket.us",
        "market_ids": ["6648798962"],
        "iab_code": "IAB12",
    },
}

DOMAIN_ALIASES = {
    "kalshi.com": {"kalshi.com"},
    "polymarket.us": {"polymarket.us", "polymarket.com"},
}
