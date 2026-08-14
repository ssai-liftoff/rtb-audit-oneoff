"""
RTB Platform Parity Audit — Part 4: Match iOS/Android Siblings

Fast two-phase matching within each publisher account:
  1. Exact match on normalized app name (strips iOS/Android/GP suffixes)
  2. Fuzzy match only for leftover apps in small accounts (≤500 cross-pairs)

Input:  output/rtb_platform_parity_audit/p3_account_portfolios.csv
Output: output/rtb_platform_parity_audit/p4_sibling_pairs.csv
"""

import os
import re
import pandas as pd
from datetime import datetime
from difflib import SequenceMatcher

OUTPUT_DIR = "output/rtb_platform_parity_audit"
MIN_FUZZY_SIMILARITY = 0.72
FUZZY_MAX_CROSS_PAIRS = 500
PROGRESS_EVERY = 250

os.makedirs(OUTPUT_DIR, exist_ok=True)

PLATFORM_SUFFIX_RE = re.compile(
    r"\b(ios|iphone|android|gp|google play|play store|app store)\b|"
    r"\((ios|android)\)|"
    r"[-–—]\s*(ios|android|gp)\b",
    re.IGNORECASE,
)
PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def normalize_name(name):
    text = str(name or "").lower().strip()
    text = PLATFORM_SUFFIX_RE.sub(" ", text)
    text = PUNCT_RE.sub(" ", text)
    return " ".join(text.split())


def fuzzy_ratio(a, b):
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def row_to_pair(ios_row, and_row, score, method):
    return {
        "account_id": ios_row["account_id"],
        "account_name": ios_row["account_name"],
        "ios_app_id": ios_row["app_id"],
        "ios_app_name": ios_row["app_name"],
        "ios_market_id": ios_row["market_id"],
        "ios_daily_spend": ios_row["daily_spend"],
        "android_app_id": and_row["app_id"],
        "android_app_name": and_row["app_name"],
        "android_market_id": and_row["market_id"],
        "android_daily_spend": and_row["daily_spend"],
        "match_score": round(score, 4),
        "match_method": method,
    }


def greedy_pair(ios_rows, android_rows, score_fn, method):
    """One-to-one pairing: highest combined spend first."""
    candidates = []
    for ios_row in ios_rows:
        for and_row in android_rows:
            score = score_fn(ios_row, and_row)
            if score >= MIN_FUZZY_SIMILARITY:
                candidates.append((
                    score,
                    float(ios_row["daily_spend"]) + float(and_row["daily_spend"]),
                    ios_row,
                    and_row,
                ))

    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    used_ios = set()
    used_android = set()
    pairs = []
    for score, _, ios_row, and_row in candidates:
        if ios_row["app_id"] in used_ios or and_row["app_id"] in used_android:
            continue
        used_ios.add(ios_row["app_id"])
        used_android.add(and_row["app_id"])
        pairs.append(row_to_pair(ios_row, and_row, score, method))
    return pairs, used_ios, used_android


def pair_account_apps(account_df):
    ios_df = account_df[account_df["platform"] == "ios"]
    android_df = account_df[account_df["platform"] == "android"]
    if ios_df.empty or android_df.empty:
        return []

    ios_rows = ios_df.to_dict("records")
    android_rows = android_df.to_dict("records")
    all_pairs = []
    matched_ios = set()
    matched_android = set()

    # Phase 1: exact normalized name
    ios_by_norm = {}
    for row in ios_rows:
        norm = row["norm_name"]
        if norm:
            ios_by_norm.setdefault(norm, []).append(row)
    android_by_norm = {}
    for row in android_rows:
        norm = row["norm_name"]
        if norm:
            android_by_norm.setdefault(norm, []).append(row)

    for norm in set(ios_by_norm) & set(android_by_norm):
        pairs, used_i, used_a = greedy_pair(
            ios_by_norm[norm],
            android_by_norm[norm],
            lambda i, a: 1.0,
            "exact_normalized",
        )
        all_pairs.extend(pairs)
        matched_ios |= used_i
        matched_android |= used_a

    # Phase 2: fuzzy for unmatched leftovers in small accounts only
    leftover_ios = [r for r in ios_rows if r["app_id"] not in matched_ios]
    leftover_android = [r for r in android_rows if r["app_id"] not in matched_android]
    cross = len(leftover_ios) * len(leftover_android)
    if leftover_ios and leftover_android and cross <= FUZZY_MAX_CROSS_PAIRS:
        pairs, _, _ = greedy_pair(
            leftover_ios,
            leftover_android,
            lambda i, a: fuzzy_ratio(i["norm_name"], a["norm_name"]),
            "fuzzy",
        )
        all_pairs.extend(pairs)

    return all_pairs


if __name__ == "__main__":
    portfolio_path = f"{OUTPUT_DIR}/p3_account_portfolios.csv"
    cache = f"{OUTPUT_DIR}/p4_sibling_pairs.csv"

    if not os.path.exists(portfolio_path):
        raise FileNotFoundError(f"Not found: {portfolio_path} — run p3_fetch_app_portfolios.py first")

    refresh = "--refresh" in __import__("sys").argv
    if refresh and os.path.exists(cache):
        os.remove(cache)
        log("Removed cached p4 output (--refresh)", "WARN")

    if os.path.exists(cache):
        log(f"Cache found — loading {cache}")
        pairs_df = pd.read_csv(cache)
    else:
        log("═" * 60, "STEP")
        log("RTB PLATFORM PARITY — PART 4: MATCH SIBLINGS", "STEP")
        log("═" * 60, "STEP")

        portfolio = pd.read_csv(portfolio_path)
        portfolio["app_id"] = portfolio["app_id"].astype(str).str.strip()
        portfolio["account_id"] = portfolio["account_id"].astype(str).str.strip()
        portfolio["norm_name"] = portfolio["app_name"].apply(normalize_name)

        # Only apps with known platform participate
        portfolio = portfolio[portfolio["platform"].isin(["ios", "android"])].copy()
        log(f"Apps with known platform: {len(portfolio):,}")

        all_pairs = []
        grouped = portfolio.groupby("account_id", sort=False)
        total = grouped.ngroups

        for i, (_, account_df) in enumerate(grouped, 1):
            all_pairs.extend(pair_account_apps(account_df))
            if i % PROGRESS_EVERY == 0 or i == total:
                log(f"  Accounts: {i:,}/{total:,}  pairs: {len(all_pairs):,}")

        pairs_df = pd.DataFrame(all_pairs)
        if pairs_df.empty:
            pairs_df = pd.DataFrame(
                columns=[
                    "account_id", "account_name",
                    "ios_app_id", "ios_app_name", "ios_market_id", "ios_daily_spend",
                    "android_app_id", "android_app_name", "android_market_id", "android_daily_spend",
                    "match_score", "match_method",
                ]
            )
        else:
            pairs_df = pairs_df.sort_values(
                ["match_score", "ios_daily_spend", "android_daily_spend"],
                ascending=False,
            ).reset_index(drop=True)

        pairs_df.to_csv(cache, index=False)
        log(f"Saved → {cache}")

    log("═" * 60, "STEP")
    log(f"Sibling pairs found: {len(pairs_df):,}")
    if len(pairs_df):
        if "match_method" in pairs_df.columns:
            log(f"  By method: {pairs_df['match_method'].value_counts().to_dict()}")
        high_conf = (pairs_df["match_score"] >= 0.9).sum()
        log(f"  High confidence (≥0.9): {high_conf:,}")
        log(f"  Unique accounts:        {pairs_df['account_id'].nunique():,}")
    log("Next: run p5_build_audit.py", "STEP")
    log("═" * 60, "STEP")
