import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

LOOKER_BASE_URL = os.getenv("LOOKER_BASE_URL", "https://liftoff.cloud.looker.com")
LOOKER_CLIENT_ID = os.getenv("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.getenv("LOOKER_CLIENT_SECRET")


def log(msg, level="INFO"):
    from datetime import datetime

    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def require_credentials():
    if not LOOKER_CLIENT_ID or not LOOKER_CLIENT_SECRET:
        raise ValueError("Missing Looker credentials in .env")


def get_token():
    require_credentials()
    resp = requests.post(
        f"{LOOKER_BASE_URL}/api/4.0/login",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=f"client_id={LOOKER_CLIENT_ID}&client_secret={LOOKER_CLIENT_SECRET}",
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    log("Authenticated with Looker")
    return token


def auth_headers(token):
    return {"Authorization": f"token {token}", "Content-Type": "application/json"}


def run_query(
    token,
    model,
    view,
    fields,
    filters,
    sorts=None,
    limit=100_000,
    offset=0,
    timeout=600,
    retries=3,
    label="",
):
    payload = {
        "model": model,
        "view": view,
        "fields": fields,
        "filters": filters,
        "limit": str(limit),
        "offset": str(offset),
    }
    if sorts:
        payload["sorts"] = sorts

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                f"{LOOKER_BASE_URL}/api/4.0/queries/run/json",
                headers=auth_headers(token),
                json=payload,
                timeout=timeout,
            )
            if not resp.ok:
                log(f"Looker error {resp.status_code}: {resp.text[:300]}", "ERROR")
                resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as err:
            last_err = err
            if attempt < retries:
                wait = 15 * attempt
                log(
                    f"  {label} timed out (attempt {attempt}/{retries}) — retrying in {wait}s...",
                    "WARN",
                )
                time.sleep(wait)
            else:
                raise last_err


def run_paginated(token, model, view, fields, filters, sorts=None, page_size=100_000, label=""):
    rows = []
    offset = 0
    while True:
        page = run_query(
            token,
            model,
            view,
            fields,
            filters,
            sorts=sorts,
            limit=page_size,
            offset=offset,
            label=label,
        )
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
        log(f"  {label} paginating, {offset:,} rows so far...")
    return rows
