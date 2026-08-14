"""
RTB Platform Parity Audit — Part 5: Build Missed-Opportunity Audit

Output always frames an unblock opportunity as:
  allowed_app  — app currently sending (on allow list, or NOT on deny list)
  missed_app   — sibling not yet unlocked (not on allow list, or ON deny list)

Allow list: allowed = on list,           missed = sibling not on list
Deny list:   allowed = not on deny list, missed = sibling on deny list (blocked)

Input:  output/rtb_platform_parity_audit/p2_connection_lists.csv
        output/rtb_platform_parity_audit/p4_sibling_pairs.csv
Output: output/rtb_platform_parity_audit/p5_allow_gaps.csv
        output/rtb_platform_parity_audit/p5_deny_gaps.csv
        output/rtb_platform_parity_audit/p5_platform_parity_audit.csv
"""

import json
import os
import pandas as pd
from datetime import datetime

OUTPUT_DIR = "output/rtb_platform_parity_audit"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def parse_id_set(raw):
    try:
        return {str(x).strip() for x in json.loads(raw or "[]") if str(x).strip()}
    except Exception:
        return set()


def sibling_lookup(pairs_df):
    """Map each app_id to its sibling pair metadata."""
    by_app = {}
    for _, row in pairs_df.iterrows():
        ios_id = str(row["ios_app_id"])
        and_id = str(row["android_app_id"])
        shared = {
            "account_id": row["account_id"],
            "account_name": row["account_name"],
            "match_score": row["match_score"],
        }
        by_app[ios_id] = {
            **shared,
            "ios_app_id": ios_id,
            "ios_app_name": row["ios_app_name"],
            "ios_market_id": row["ios_market_id"],
            "ios_daily_spend": row["ios_daily_spend"],
            "android_app_id": and_id,
            "android_app_name": row["android_app_name"],
            "android_market_id": row["android_market_id"],
            "android_daily_spend": row["android_daily_spend"],
        }
        by_app[and_id] = by_app[ios_id]
    return by_app


def app_fields(pair, app_id):
    app_id = str(app_id)
    if app_id == str(pair["ios_app_id"]):
        return {
            "app_id": app_id,
            "app_name": pair["ios_app_name"],
            "platform": "ios",
            "market_id": pair["ios_market_id"],
            "daily_spend": pair["ios_daily_spend"],
        }
    return {
        "app_id": app_id,
        "app_name": pair["android_app_name"],
        "platform": "android",
        "market_id": pair["android_market_id"],
        "daily_spend": pair["android_daily_spend"],
    }


def sibling_id(pair, app_id):
    app_id = str(app_id)
    return str(pair["android_app_id"] if app_id == str(pair["ios_app_id"]) else pair["ios_app_id"])


def parse_allow_platform(conn):
    raw = conn.get("allow_platform")
    if pd.isna(raw) or str(raw).strip() in ("", "nan", "{}"):
        ios = conn.get("allow_platform_ios")
        android = conn.get("allow_platform_android")
        if pd.notna(ios) or pd.notna(android):
            return {
                "ios": bool(ios),
                "android": bool(android),
                "amazon": bool(conn.get("allow_platform_amazon", False)),
                "windows": bool(conn.get("allow_platform_windows", False)),
            }
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def gap_relevant(missed_platform, allow_platform):
    """Opp is real only if the connection supports the missed app's platform."""
    if not allow_platform:
        return True
    return bool(allow_platform.get(missed_platform, False))


def make_row(conn, list_type, allowed, missed, pair):
    gap_type = (
        "allow_list_missed_sibling"
        if list_type == "allow"
        else "deny_list_blocked_sibling"
    )
    return {
        "rtb_connection_id": str(conn["rtb_connection_id"]),
        "rtb_connection_name": conn["rtb_connection_name"],
        "rtb_account_id": conn.get("rtb_account_id", ""),
        "rtb_account_name": conn.get("rtb_account_name", ""),
        "connection_daily_spend": conn.get("daily_spend", 0),
        "supported_platforms": conn.get("supported_platforms", "unknown"),
        "allow_platform_ios": conn.get("allow_platform_ios", False),
        "allow_platform_android": conn.get("allow_platform_android", False),
        "list_type": list_type,
        "allowed_app_id": allowed["app_id"],
        "allowed_app_name": allowed["app_name"],
        "allowed_platform": allowed["platform"],
        "allowed_market_id": allowed["market_id"],
        "allowed_daily_spend": allowed["daily_spend"],
        "missed_app_id": missed["app_id"],
        "missed_app_name": missed["app_name"],
        "missed_platform": missed["platform"],
        "missed_market_id": missed["market_id"],
        "missed_daily_spend": missed["daily_spend"],
        "publisher_account_id": pair["account_id"],
        "publisher_account_name": pair["account_name"],
        "sibling_match_score": pair["match_score"],
        "gap_type": gap_type,
    }


def build_gaps(connections_df, pairs_df, list_type):
    lookup = sibling_lookup(pairs_df)
    rows = []

    for _, conn in connections_df[connections_df["list_type"] == list_type].iterrows():
        allow_platform = parse_allow_platform(conn)
        list_ids = parse_id_set(
            conn.get("allowed_app_ids") if list_type == "allow" else conn.get("denied_app_ids")
        )

        if list_type == "allow":
            # Allowed = on list; missed = sibling not on list
            for app_id in list_ids:
                pair = lookup.get(app_id)
                if not pair:
                    continue
                other_id = sibling_id(pair, app_id)
                if other_id in list_ids:
                    continue
                allowed = app_fields(pair, app_id)
                missed = app_fields(pair, other_id)
                if not gap_relevant(missed["platform"], allow_platform):
                    continue
                rows.append(make_row(conn, list_type, allowed, missed, pair))
        else:
            # Allowed = NOT on deny list; missed = sibling ON deny list (blocked)
            for blocked_id in list_ids:
                pair = lookup.get(blocked_id)
                if not pair:
                    continue
                allowed_id = sibling_id(pair, blocked_id)
                if allowed_id in list_ids:
                    continue
                allowed = app_fields(pair, allowed_id)
                missed = app_fields(pair, blocked_id)
                if not gap_relevant(missed["platform"], allow_platform):
                    continue
                rows.append(make_row(conn, list_type, allowed, missed, pair))

    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(
            ["connection_daily_spend", "missed_daily_spend", "sibling_match_score"],
            ascending=[False, False, False],
        ).reset_index(drop=True)
    return out


if __name__ == "__main__":
    p2_path = f"{OUTPUT_DIR}/p2_connection_lists.csv"
    p4_path = f"{OUTPUT_DIR}/p4_sibling_pairs.csv"

    for path in [p2_path, p4_path]:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing: {path} — run prior steps first")

    log("═" * 60, "STEP")
    log("RTB PLATFORM PARITY — PART 5: BUILD AUDIT", "STEP")
    log("═" * 60, "STEP")

    connections = pd.read_csv(p2_path)
    pairs = pd.read_csv(p4_path)

    allow_gaps = build_gaps(connections, pairs, "allow")
    deny_gaps = build_gaps(connections, pairs, "deny")

    allow_path = f"{OUTPUT_DIR}/p5_allow_gaps.csv"
    deny_path = f"{OUTPUT_DIR}/p5_deny_gaps.csv"
    combined_path = f"{OUTPUT_DIR}/p5_platform_parity_audit.csv"

    allow_gaps.to_csv(allow_path, index=False)
    deny_gaps.to_csv(deny_path, index=False)
    pd.concat([allow_gaps, deny_gaps], ignore_index=True).to_csv(combined_path, index=False)

    log(f"Allow-list opps:  {len(allow_gaps):,} → {allow_path}")
    log(f"Deny-list opps:   {len(deny_gaps):,} → {deny_path}")
    log(f"Combined audit:   {len(allow_gaps) + len(deny_gaps):,} → {combined_path}")

    if len(allow_gaps):
        log("Top allow-list opps (by missed app spend):", "STEP")
        for _, row in allow_gaps.nlargest(5, "missed_daily_spend").iterrows():
            log(
                f"  {row['rtb_connection_name'][:40]:<40} "
                f"allowed {row['allowed_app_name'][:22]} → missed {row['missed_app_name'][:22]} "
                f"(${row['missed_daily_spend']:,.0f}/day)"
            )

    if len(deny_gaps):
        log("Top deny-list opps (by blocked/missed app spend):", "STEP")
        for _, row in deny_gaps.nlargest(5, "missed_daily_spend").iterrows():
            log(
                f"  {row['rtb_connection_name'][:40]:<40} "
                f"allowed {row['allowed_app_name'][:22]} → blocked {row['missed_app_name'][:22]} "
                f"(${row['missed_daily_spend']:,.0f}/day)"
            )

    log("═" * 60, "STEP")
    log("PART 5 COMPLETE", "STEP")
    log("═" * 60, "STEP")
