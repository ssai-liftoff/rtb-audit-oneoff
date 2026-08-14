"""
Fraud Banking Audit — Part 3: Fetch L60D Spend Data from Looker

Fetches last-60-day spend, publisher revenue, and net revenue for every
newly flagged potential fraud account from Part 2. Also pulls account
metadata: AM name, contact email, created date, and is_deleted status.

Requests are batched in chunks of 500 pub IDs to stay within Looker limits.

Inputs:
  - output/fraud_banking_audit/p2_matches.csv

Outputs:
  - output/fraud_banking_audit/p3_spend_data.csv
"""

import os
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")

OUTPUT_DIR = "output/fraud_banking_audit"
MATCHES_CSV = f"{OUTPUT_DIR}/p2_matches.csv"
LOOKBACK_DAYS = 60
BATCH_SIZE = 5000
os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def get_looker_token():
    log("Authenticating with Looker API...", "STEP")
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}"
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    log("Authenticated successfully")
    return token


def looker_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def run_query(token, model, view, fields, filters, sorts=None, limit=100000):
    payload = {
        "model": model,
        "view": view,
        "fields": fields,
        "filters": filters,
        "limit": str(limit)
    }
    if sorts:
        payload["sorts"] = sorts
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
        headers=looker_headers(token),
        json=payload
    )
    if not resp.ok:
        log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
        resp.raise_for_status()
    return resp.json()


def fetch_metadata_batch(token, pub_ids_batch, batch_num, total_batches):
    """Fetch account metadata (AM, email, created date) with a broad date range.
    Using all-time range ensures we get AM info even for inactive accounts."""
    id_filter = ",".join(str(pid) for pid in pub_ids_batch)
    data = run_query(
        token,
        model="dmx_reports",
        view="publisher_report",
        fields=[
            "publisher_accounts.id",
            "publisher_accounts.name",
            "publisher_accounts.is_deleted",
            "publisher_accounts.contact_email",
            "publisher_accounts.created_date",
            "salesforce_accounts_monetize.am_user_name",
        ],
        filters={
            "publisher_report.event_date": "NOT NULL",
            "publisher_accounts.id": id_filter,
        },
        limit=100000
    )
    log(f"  Metadata batch {batch_num}/{total_batches}: {len(pub_ids_batch)} IDs → {len(data)} rows")
    return data


def fetch_spend_batch(token, pub_ids_batch, batch_num, total_batches):
    """Fetch L60D spend metrics for a batch of pub IDs."""
    id_filter = ",".join(str(pid) for pid in pub_ids_batch)
    data = run_query(
        token,
        model="dmx_reports",
        view="publisher_report",
        fields=[
            "publisher_accounts.id",
            "publisher_report.unified_ad_spend",
            "publisher_report.publisher_payout",
            "publisher_report.unified_net_revenue",
        ],
        filters={
            "publisher_report.event_date": f"{LOOKBACK_DAYS} days ago for {LOOKBACK_DAYS} days",
            "publisher_accounts.id": id_filter,
        },
        sorts=["publisher_report.unified_ad_spend desc"],
        limit=100000
    )
    log(f"  Spend batch {batch_num}/{total_batches}: {len(pub_ids_batch)} IDs → {len(data)} rows")
    return data


def fetch_all_spend(token, pub_ids):
    """Batch-fetch metadata + L60D spend for all flagged pub IDs.

    Two separate queries:
      1. Broad date range — to capture AM / account info for ALL publishers,
         including those with no recent activity.
      2. 60-day range — for the spend metrics only.
    """
    cache = f"{OUTPUT_DIR}/p3_spend_data.csv"
    if os.path.exists(cache):
        log(f"Loading spend data from cache: {cache}")
        return pd.read_csv(cache, dtype={"pub_id": str})

    batches = [pub_ids[i:i + BATCH_SIZE] for i in range(0, len(pub_ids), BATCH_SIZE)]
    total = len(batches)

    # ── Query 1: account metadata (all time) ─────────────────────────────────
    log(f"Fetching account metadata (all time) for {len(pub_ids)} accounts in {total} batches...", "STEP")
    meta_rows = []
    for i, batch in enumerate(batches, 1):
        meta_rows.extend(fetch_metadata_batch(token, batch, i, total))

    if meta_rows:
        meta_df = pd.DataFrame(meta_rows)
        meta_df.columns = ["pub_id", "account_name", "is_deleted", "account_email",
                           "date_created", "am_name"]
        meta_df = (meta_df.groupby("pub_id", as_index=False)
                          .agg({c: "first" for c in ["account_name", "is_deleted",
                                                      "account_email", "date_created", "am_name"]}))
    else:
        log("No metadata returned — account info columns will be blank.", "WARN")
        meta_df = pd.DataFrame(columns=["pub_id", "account_name", "is_deleted",
                                         "account_email", "date_created", "am_name"])

    # ── Query 2: L60D spend metrics ───────────────────────────────────────────
    log(f"Fetching L60D spend for {len(pub_ids)} accounts in {total} batches...", "STEP")
    spend_rows = []
    for i, batch in enumerate(batches, 1):
        spend_rows.extend(fetch_spend_batch(token, batch, i, total))

    if spend_rows:
        spend_df = pd.DataFrame(spend_rows)
        spend_df.columns = ["pub_id", "l60d_spend", "l60d_pub_revenue", "l60d_unr"]
        for col in ["l60d_spend", "l60d_pub_revenue", "l60d_unr"]:
            spend_df[col] = pd.to_numeric(spend_df[col], errors="coerce").fillna(0)
        spend_df = (spend_df.groupby("pub_id", as_index=False)
                            .agg({"l60d_spend": "sum", "l60d_pub_revenue": "sum", "l60d_unr": "sum"}))
        for col in ["l60d_spend", "l60d_pub_revenue", "l60d_unr"]:
            spend_df[col] = spend_df[col].round(2)
    else:
        log("No L60D spend data returned.", "WARN")
        spend_df = pd.DataFrame(columns=["pub_id", "l60d_spend", "l60d_pub_revenue", "l60d_unr"])

    # ── Merge metadata + spend ────────────────────────────────────────────────
    # Start from all pub_ids so every publisher has a row even with no Looker data
    all_ids_df = pd.DataFrame({"pub_id": pub_ids})
    df = all_ids_df.merge(meta_df,  on="pub_id", how="left")
    df = df.merge(spend_df, on="pub_id", how="left")
    df[["l60d_spend", "l60d_pub_revenue", "l60d_unr"]] = (
        df[["l60d_spend", "l60d_pub_revenue", "l60d_unr"]].fillna(0)
    )

    df.to_csv(cache, index=False)
    log(f"Spend data saved → {cache}")
    log(f"  Total publishers: {len(df)} | With metadata: {df['account_name'].notna().sum()} | With L60D spend: {(df['l60d_spend'] > 0).sum()}")
    return df


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")

    if not os.path.exists(MATCHES_CSV):
        raise FileNotFoundError(f"Matches file not found at {MATCHES_CSV}. Run p2 first.")

    log("=" * 60, "STEP")
    log("FRAUD BANKING AUDIT — PART 3: FETCH L60D SPEND DATA", "STEP")
    log("=" * 60, "STEP")

    matches_df = pd.read_csv(MATCHES_CSV, dtype=str)
    # Exclude typo-only accounts that have no real IVT cross-match (ivt_pub_id = "N/A")
    # but still fetch their spend — they're still potential fraud
    pub_ids = matches_df["new_fraud_pub_id"].dropna().unique().tolist()
    pub_ids = [p for p in pub_ids if str(p).strip() and str(p).strip() != "N/A"]
    log(f"Unique new fraud pub IDs to fetch spend for: {len(pub_ids)}")

    token = get_looker_token()
    spend_df = fetch_all_spend(token, pub_ids)

    log("=" * 60, "STEP")
    log("PART 3 COMPLETE", "STEP")
    log(f"  Accounts with spend data:  {len(spend_df)}", "STEP")
    log("Next: run p4_build_final.py", "STEP")
    log("=" * 60, "STEP")
