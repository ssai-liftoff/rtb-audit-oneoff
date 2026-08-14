"""
Fraud Banking Audit — Part 1: Fetch IVT Accounts from Looker

Fetches all publisher accounts whose name contains "IVT -" with no date
filter (all historical records). These become the seed set for the banking
field cross-matching in Part 2.

Outputs:
  - output/fraud_banking_audit/p1_ivt_accounts.csv
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


def fetch_ivt_accounts(token):
    cache = f"{OUTPUT_DIR}/p1_ivt_accounts.csv"
    if os.path.exists(cache):
        log(f"Loading IVT accounts from cache: {cache}")
        return pd.read_csv(cache, dtype=str)

    log("Fetching all IVT-tagged publisher accounts from Looker (all time)...", "STEP")
    log("Filter: publisher_accounts.name contains 'IVT -'")
    log("Please wait...")

    # publisher_report is event-based and requires a date filter to return rows.
    # Use a broad historical range to capture all IVT accounts regardless of when
    # they were active. 3650 days = 10 years, covers the full platform history.
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
        ],
        filters={
            "publisher_report.event_date": "NOT NULL",  # equivalent to "is any time"
            "publisher_accounts.name": "%IVT -%",
        },
        sorts=["publisher_accounts.name asc"],
        limit=100000
    )

    log(f"Raw rows returned: {len(data)}")

    if not data:
        log("No IVT accounts returned. Check Looker filter or credentials.", "WARN")
        return pd.DataFrame(columns=["pub_id", "pub_name", "is_deleted", "contact_email", "created_date"])

    df = pd.DataFrame(data)
    df.columns = ["pub_id", "pub_name", "is_deleted", "contact_email", "created_date"]
    df = df.drop_duplicates(subset=["pub_id"])
    df = df[df["pub_id"].notna() & (df["pub_id"].astype(str).str.strip() != "")]

    log(f"Unique IVT publisher accounts: {len(df)}")
    df.to_csv(cache, index=False)
    log(f"Saved → {cache}")
    return df


if __name__ == "__main__":
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env (LOOKER_CLIENT_ID / LOOKER_CLIENT_SECRET)")

    log("=" * 60, "STEP")
    log("FRAUD BANKING AUDIT — PART 1: FETCH IVT ACCOUNTS", "STEP")
    log("=" * 60, "STEP")

    token = get_looker_token()
    df = fetch_ivt_accounts(token)

    log("=" * 60, "STEP")
    log("PART 1 COMPLETE", "STEP")
    log(f"  IVT accounts fetched: {len(df)}", "STEP")
    log("Next: run p2_run_matching.py", "STEP")
    log("=" * 60, "STEP")
