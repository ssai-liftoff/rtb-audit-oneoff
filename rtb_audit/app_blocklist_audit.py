"""
Part 2: For each RTB connection, fetch allow/deny list from API,
derive blocked high-spend apps, and build the final audit output.

Requires output/ from Part 1 to exist.
Output: output/rtb_blocked_app_audit.csv
"""

import os
import time
import requests
import pandas as pd
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


# ── Fetch allow/deny list from RTB API ───────────────────────────────────────

def fetch_rtb_app_filter(connection_id):
    """
    Returns a dict with:
      - filter_type: 'allow', 'deny', or 'none'
      - app_ids: list of app IDs in that list
    """
    try:
        resp = requests.get(
            f"{RTB_API_BASE}/{connection_id}",
            headers=HEADERS,
            timeout=10
        )
        if resp.status_code == 401:
            raise ValueError("RTB API token expired — please refresh RTB_API_TOKEN in .env")
        resp.raise_for_status()
        data = resp.json()

        allowed = data.get("allowedApplicationIds", [])
        denied = data.get("deniedApplicationIds", [])

        if allowed:
            return {"filter_type": "allow", "app_ids": allowed}
        elif denied:
            return {"filter_type": "deny", "app_ids": denied}
        else:
            return {"filter_type": "none", "app_ids": []}

    except ValueError:
        raise
    except Exception as e:
        print(f"  ⚠ Error fetching connection {connection_id}: {e}")
        return {"filter_type": "error", "app_ids": []}


# ── Derive blocked apps ───────────────────────────────────────────────────────

def get_blocked_app_ids(filter_type, filter_app_ids, all_high_spend_app_ids):
    """
    - deny list  → blocked = apps IN the deny list that are also high spend
    - allow list → blocked = high spend apps NOT in the allow list (indirect block)
    - none       → no blocks
    """
    filter_set = set(filter_app_ids)
    high_spend_set = set(all_high_spend_app_ids)

    if filter_type == "deny":
        return list(high_spend_set & filter_set)
    elif filter_type == "allow":
        return list(high_spend_set - filter_set)
    else:
        return []


# ── Main audit loop ───────────────────────────────────────────────────────────

def build_audit():
    # Load Part 1 outputs
    print("Loading Part 1 data...")
    rtb_df = pd.read_csv("output/rtb_connections.csv")
    apps_df = pd.read_csv("output/high_spend_apps.csv")

    print(f"  RTB connections: {len(rtb_df)}")
    print(f"  High-spend apps: {len(apps_df)}")

    all_high_spend_app_ids = apps_df["app_id"].dropna().astype(str).tolist()

    # Build app lookup for quick joins
    app_lookup = apps_df.set_index("app_id")[["app_name", "account_id", "account_name", "daily_revenue"]].to_dict("index")

    # Total RTB daily spend — used to compute each connection's share
    total_rtb_daily_spend = rtb_df["daily_spend"].sum()
    print(f"  Total RTB daily spend: ${total_rtb_daily_spend:,.2f}")

    rows = []
    total = len(rtb_df)

    print(f"\nFetching app filter lists for {total} RTB connections...")

    for i, rtb_row in rtb_df.iterrows():
        connection_id = str(rtb_row["rtb_connection_id"])
        connection_name = rtb_row["rtb_connection_name"]
        rtb_daily_spend = rtb_row["daily_spend"]

        print(f"  [{i+1}/{total}] {connection_name} ({connection_id})")

        # Fetch allow/deny list from API
        result = fetch_rtb_app_filter(connection_id)
        filter_type = result["filter_type"]

        if filter_type == "error":
            continue

        # Derive which qualifying apps are blocked
        blocked_ids = get_blocked_app_ids(filter_type, result["app_ids"], all_high_spend_app_ids)

        if not blocked_ids:
            print(f"    → No blocked apps (filter_type: {filter_type})")
            continue

        print(f"    → {len(blocked_ids)} blocked apps (filter_type: {filter_type})")

        # RTB connection's share of total RTB spend
        rtb_share = rtb_daily_spend / total_rtb_daily_spend if total_rtb_daily_spend > 0 else 0

        # Build a row per blocked app
        for app_id in blocked_ids:
            app_info = app_lookup.get(app_id, {})
            app_daily_revenue = app_info.get("daily_revenue", 0)

            # Uplift = this connection's share of total RTB spend × app's daily revenue
            # Intuition: connection represents X% of all RTB demand; if unblocked it would
            # likely capture roughly X% of what this app already earns from RTB.
            estimated_uplift = round(rtb_share * app_daily_revenue, 2)

            rows.append({
                "rtb_connection_id": connection_id,
                "rtb_connection_name": connection_name,
                "rtb_daily_spend": round(rtb_daily_spend, 2),
                "filter_type": filter_type,
                "blocked_app_id": app_id,
                "blocked_app_name": app_info.get("app_name", ""),
                "blocked_account_id": app_info.get("account_id", ""),
                "blocked_account_name": app_info.get("account_name", ""),
                "app_daily_revenue": round(app_daily_revenue, 2),
                "estimated_uplift": estimated_uplift,
            })

        # Small delay to avoid hammering the API
        time.sleep(0.1)

    if not rows:
        print("\nNo blocked high-spend apps found across all connections.")
        return

    # Build final dataframe
    output_df = pd.DataFrame(rows)

    # Sort by estimated uplift descending
    output_df = output_df.sort_values("estimated_uplift", ascending=False).reset_index(drop=True)

    # Save
    output_path = "output/rtb_blocked_app_audit.csv"
    output_df.to_csv(output_path, index=False)

    print(f"\n{'='*50}")
    print(f"Part 2 complete!")
    print(f"  Total blocked app opportunities: {len(output_df)}")
    print(f"  Unique RTB connections affected: {output_df['rtb_connection_id'].nunique()}")
    print(f"  Unique blocked apps: {output_df['blocked_app_id'].nunique()}")
    print(f"  Saved to: {output_path}")
    print(f"{'='*50}")
    print(f"\nTop 5 opportunities:")
    print(output_df[["rtb_connection_name", "blocked_app_name", "rtb_daily_spend", "app_daily_revenue", "estimated_uplift"]].head())


if __name__ == "__main__":
    if not RTB_API_TOKEN:
        raise ValueError("Missing RTB_API_TOKEN in .env — grab a fresh Bearer token from DevTools")

    build_audit()