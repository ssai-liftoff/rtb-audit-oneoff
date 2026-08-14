"""
Part 2b: For each RTB connection, fetch allowed countries + placement types from API.
Aggregate per RTB account, compare against network top 25 countries and placement types.

Outputs:
  - output/audit_missed_geos.csv
  - output/audit_missed_placements.csv

Requires Part 1b outputs to exist in output/
"""

import os
import time
import requests
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()

RTB_API_TOKEN = os.getenv("RTB_API_TOKEN")
RTB_API_BASE = "https://pub-ctrl-api.vungle.com/api/v1/rtbconnections"

HEADERS = {
    "authorization": f"Bearer {RTB_API_TOKEN}",
    "vungle-source": "admin",
    "vungle-version": "1",
    "content-type": "application/json"
}

os.makedirs("output", exist_ok=True)


# ── Fetch connection config from API ──────────────────────────────────────────

def fetch_connection_config(connection_id):
    try:
        resp = requests.get(
            f"{RTB_API_BASE}/{connection_id}",
            headers=HEADERS,
            timeout=10
        )
        if resp.status_code == 401:
            raise ValueError("RTB API token expired — refresh RTB_API_TOKEN in .env")
        resp.raise_for_status()
        data = resp.json()

        # Countries — empty list means all countries allowed
        allowed_countries = data.get("allowedCountries", [])
        is_all_countries = data.get("isAllCountries", False) or len(allowed_countries) == 0

        # Placement types — normalise to lowercase for comparison
        placement_types = [p.lower() for p in data.get("supportedImpressionType", [])]

        return {
            "is_all_countries": is_all_countries,
            "allowed_countries": [c.upper().strip() for c in allowed_countries],
            "placement_types": placement_types
        }

    except ValueError:
        raise
    except Exception as e:
        print(f"  ⚠ Error fetching connection {connection_id}: {e}")
        return None


# ── Main audit ────────────────────────────────────────────────────────────────

def build_audit():
    print("Loading Part 1b data...")
    rtb_df = pd.read_csv("output/rtb_accounts.csv")
    countries_df = pd.read_csv("output/top25_countries.csv")
    placements_df = pd.read_csv("output/top_placement_types.csv")

    # Total network spend for uplift calculation
    total_country_spend = countries_df["total_spend_7d"].sum()
    total_placement_spend = placements_df["total_spend_7d"].sum()

    # Build lookup dicts for spend
    country_spend_lookup = countries_df.set_index("geo_code")[["daily_spend", "pct_of_network", "rank"]].to_dict("index")
    placement_spend_lookup = placements_df.set_index("placement_type_api")[["daily_spend", "pct_of_network", "rank"]].to_dict("index")

    top25_countries = set(countries_df["geo_code"].tolist())
    top_placements = set(placements_df["placement_type_api"].tolist())

    # Group connections by account
    accounts = rtb_df.groupby("rtb_account_id")

    # Per-account aggregated config
    account_configs = {}  # rtb_account_id → {countries: set, placements: set}

    total_connections = len(rtb_df)
    print(f"\nFetching API config for {total_connections} connections across {len(accounts)} accounts...")

    connection_rows = list(rtb_df.itertuples(index=False))

    def fetch_row(row):
        connection_id = str(row.rtb_connection_id)
        account_id = str(row.rtb_account_id)
        time.sleep(0.05)  # 50ms stagger per thread to stay under rate limit
        config = fetch_connection_config(connection_id)
        return account_id, connection_id, config

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(fetch_row, row): row for row in connection_rows}
        done = 0
        for future in as_completed(futures):
            done += 1
            account_id, connection_id, config = future.result()
            if config is None:
                continue

            if account_id not in account_configs:
                account_configs[account_id] = {
                    "is_all_countries": False,
                    "countries": set(),
                    "placements": set()
                }

            if config["is_all_countries"]:
                account_configs[account_id]["is_all_countries"] = True
            else:
                account_configs[account_id]["countries"].update(config["allowed_countries"])

            account_configs[account_id]["placements"].update(config["placement_types"])

            if done % 50 == 0 or done == total_connections:
                print(f"  {done}/{total_connections} connections fetched")

    # Build account-level metadata lookup
    account_meta = rtb_df.groupby("rtb_account_id").agg(
        rtb_account_name=("rtb_account_name", "first"),
        rtb_contact_name=("rtb_contact_name", "first"),
        account_daily_spend=("daily_spend", "sum")
    ).reset_index()
    meta_lookup = account_meta.set_index("rtb_account_id").to_dict("index")

    # ── Build missed GEOs table ───────────────────────────────────────────────
    print("\nBuilding missed GEOs table...")
    geo_rows = []

    for account_id, config in account_configs.items():
        meta = meta_lookup.get(account_id, {})
        account_name = meta.get("rtb_account_name", "")
        contact_name = meta.get("rtb_contact_name", "")
        account_daily_spend = meta.get("account_daily_spend", 0)

        # If all countries allowed → no missed geos
        if config["is_all_countries"]:
            continue

        allowed = config["countries"]
        missed_geos = top25_countries - allowed

        for geo in missed_geos:
            geo_info = country_spend_lookup.get(geo, {})
            geo_daily_spend = geo_info.get("daily_spend", 0)
            pct_of_network = geo_info.get("pct_of_network", 0)
            rank = geo_info.get("rank", "")

            # Uplift = geo's % of network spend × RTB account daily spend
            uplift = (pct_of_network / 100) * account_daily_spend

            geo_rows.append({
                "rtb_account_id": account_id,
                "rtb_account_name": account_name,
                "rtb_contact_name": contact_name,
                "rtb_daily_spend": round(account_daily_spend, 2),
                "missed_geo": geo,
                "geo_network_rank": rank,
                "geo_daily_spend": round(geo_daily_spend, 2),
                "geo_pct_of_network": pct_of_network,
                "potential_uplift": round(uplift, 2)
            })

    geo_output = pd.DataFrame(geo_rows)
    if not geo_output.empty:
        geo_output = geo_output.sort_values("potential_uplift", ascending=False).reset_index(drop=True)
    geo_output.to_csv("output/audit_missed_geos.csv", index=False)
    print(f"✓ {len(geo_output)} missed geo opportunities → output/audit_missed_geos.csv")

    # ── Build missed placements table ─────────────────────────────────────────
    print("Building missed placements table...")
    placement_rows = []

    for account_id, config in account_configs.items():
        meta = meta_lookup.get(account_id, {})
        account_name = meta.get("rtb_account_name", "")
        contact_name = meta.get("rtb_contact_name", "")
        account_daily_spend = meta.get("account_daily_spend", 0)

        supported = config["placements"]
        missed_placements = top_placements - supported

        for placement in missed_placements:
            placement_info = placement_spend_lookup.get(placement, {})
            placement_daily_spend = placement_info.get("daily_spend", 0)
            pct_of_network = placement_info.get("pct_of_network", 0)
            rank = placement_info.get("rank", "")

            # Uplift = placement's % of network spend × RTB account daily spend
            uplift = (pct_of_network / 100) * account_daily_spend

            placement_rows.append({
                "rtb_account_id": account_id,
                "rtb_account_name": account_name,
                "rtb_contact_name": contact_name,
                "rtb_daily_spend": round(account_daily_spend, 2),
                "missed_placement_type": placement,
                "placement_network_rank": rank,
                "placement_daily_spend": round(placement_daily_spend, 2),
                "placement_pct_of_network": pct_of_network,
                "potential_uplift": round(uplift, 2)
            })

    placement_output = pd.DataFrame(placement_rows)
    if not placement_output.empty:
        placement_output = placement_output.sort_values("potential_uplift", ascending=False).reset_index(drop=True)
    placement_output.to_csv("output/audit_missed_placements.csv", index=False)
    print(f"✓ {len(placement_output)} missed placement opportunities → output/audit_missed_placements.csv")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"Part 2b complete!")
    print(f"  Accounts with missed GEOs: {geo_output['rtb_account_id'].nunique() if not geo_output.empty else 0}")
    print(f"  Accounts with missed placements: {placement_output['rtb_account_id'].nunique() if not placement_output.empty else 0}")
    print(f"\nTop 5 GEO opportunities:")
    if not geo_output.empty:
        print(geo_output[["rtb_account_name", "missed_geo", "rtb_daily_spend", "geo_pct_of_network", "potential_uplift"]].head())
    print(f"\nTop 5 placement opportunities:")
    if not placement_output.empty:
        print(placement_output[["rtb_account_name", "missed_placement_type", "rtb_daily_spend", "placement_pct_of_network", "potential_uplift"]].head())
    print(f"{'='*50}")


if __name__ == "__main__":
    if not RTB_API_TOKEN:
        raise ValueError("Missing RTB_API_TOKEN in .env")
    build_audit()