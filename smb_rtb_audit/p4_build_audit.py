"""
SMB RTB App Blocklist Audit — Part 4: Build Audit + Calculate Uplift

For each SMB app × qualifying RTB connection, determines blocked status
and calculates estimated daily uplift using the penetration rate method:

  penetration_rate     = app_spend_on_available_connections
                         ÷ total_network_spend_of_those_connections

  estimated_uplift     = blocked_connection_daily_spend × penetration_rate

This avoids underestimating uplift for apps that are barely spending because
they are blocked from most connections — the rate is calibrated only on
connections where the app is genuinely available.

Inputs:
  - smb_rtb_audit/smb_apps.csv                         (app_id, optionally app_name)
  - output/smb_rtb_audit/p2_qualifying_accounts.csv
  - output/smb_rtb_audit/p3_connection_configs.csv
  - output/smb_rtb_audit/p1_app_connection_spend.csv

Output:
  - output/smb_rtb_audit/p4_smb_audit.csv
"""

import os
import json
import pandas as pd
from datetime import datetime

OUTPUT_DIR = "output/smb_rtb_audit"
os.makedirs(OUTPUT_DIR, exist_ok=True)

SPEND_THRESHOLD_DAILY = 1000


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def is_blocked(app_id, list_type, allowed_set, denied_set):
    """True if the app is blocked on this connection."""
    if list_type == "allow":
        return app_id not in allowed_set
    elif list_type == "deny":
        return app_id in denied_set
    return False  # no list → open to all


def parse_id_list(raw):
    """Safely parse a JSON array string from the configs CSV."""
    try:
        return set(json.loads(raw)) if pd.notna(raw) else set()
    except Exception:
        return set()


if __name__ == "__main__":
    log("═" * 55, "STEP")
    log("SMB RTB AUDIT — PART 4: BUILD AUDIT + UPLIFT", "STEP")
    log("═" * 55, "STEP")

    # ── Load inputs ───────────────────────────────────────────────────────────

    smb_path = "smb_rtb_audit/smb_apps.csv"
    if not os.path.exists(smb_path):
        raise FileNotFoundError(f"SMB app list not found: {smb_path}")

    smb_df = pd.read_csv(smb_path)
    smb_df["app_id"] = smb_df["app_id"].astype(str)
    smb_app_ids = smb_df["app_id"].dropna().tolist()[:500]
    log(f"Loaded {len(smb_app_ids)} SMB apps (top 500 by spend)")

    for path in [
        f"{OUTPUT_DIR}/p2_qualifying_accounts.csv",
        f"{OUTPUT_DIR}/p3_connection_configs.csv",
        f"{OUTPUT_DIR}/p1_app_connection_spend.csv"
    ]:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Required input not found: {path}")

    p2_df = pd.read_csv(f"{OUTPUT_DIR}/p2_qualifying_accounts.csv")
    p3_df = pd.read_csv(f"{OUTPUT_DIR}/p3_connection_configs.csv")
    app_spend_df = pd.read_csv(f"{OUTPUT_DIR}/p1_app_connection_spend.csv")

    app_spend_df["app_id"] = app_spend_df["app_id"].astype(str)
    app_spend_df["rtb_connection_id"] = app_spend_df["rtb_connection_id"].astype(str)

    # ── Build qualifying connection table ─────────────────────────────────────

    # One row per connection with spend metadata + API config
    conn_meta = (
        p2_df[p2_df["daily_spend"] >= SPEND_THRESHOLD_DAILY]
        .drop_duplicates(subset=["rtb_connection_id"])
        [["rtb_account_id", "rtb_account_name", "rtb_contact_name",
          "rtb_connection_id", "rtb_connection_name", "daily_spend"]]
        .copy()
    )
    conn_meta["rtb_connection_id"] = conn_meta["rtb_connection_id"].astype(str)

    connections = conn_meta.merge(
        p3_df[["rtb_connection_id", "list_type", "allowed_app_ids", "denied_app_ids"]],
        on="rtb_connection_id",
        how="inner"  # only connections we have config for
    )
    connections = connections[connections["list_type"] != "error"].copy()
    log(f"Qualifying connections with valid config: {len(connections)}")

    # ── Aggregate app × connection daily spend ────────────────────────────────

    app_conn_spend = (
        app_spend_df
        .groupby(["app_id", "rtb_connection_id"])["daily_spend"]
        .sum()
        .reset_index()
        .rename(columns={"daily_spend": "app_daily_spend_on_conn"})
    )

    # ── Evaluate blocked / included per app × connection ──────────────────────

    log("Evaluating blocklist status for all app × connection pairs...", "STEP")

    blocked_rows = []
    included_rows = []

    for _, conn in connections.iterrows():
        conn_id = str(conn["rtb_connection_id"])
        list_type = conn["list_type"] if pd.notna(conn["list_type"]) else "none"
        allowed_set = parse_id_list(conn["allowed_app_ids"])
        denied_set = parse_id_list(conn["denied_app_ids"])
        conn_daily_spend = conn["daily_spend"]

        # Pre-fetch app spend on this connection for all SMB apps at once
        conn_app_spend = app_conn_spend[
            app_conn_spend["rtb_connection_id"] == conn_id
        ].set_index("app_id")["app_daily_spend_on_conn"].to_dict()

        for app_id in smb_app_ids:
            app_spend_here = conn_app_spend.get(app_id, 0.0)
            blocked = is_blocked(app_id, list_type, allowed_set, denied_set)

            if blocked:
                blocked_rows.append({
                    "app_id": app_id,
                    "rtb_connection_id": conn_id,
                    "rtb_connection_name": conn["rtb_connection_name"],
                    "rtb_account_id": str(conn["rtb_account_id"]),
                    "rtb_account_name": conn["rtb_account_name"],
                    "rtb_contact_name": conn.get("rtb_contact_name", ""),
                    "list_type": list_type,
                    "conn_daily_spend": conn_daily_spend,
                    "app_spend_on_conn": app_spend_here
                })
            else:
                included_rows.append({
                    "app_id": app_id,
                    "rtb_connection_id": conn_id,
                    "conn_daily_spend": conn_daily_spend,
                    "app_spend_on_conn": app_spend_here
                })

    log(f"  Blocked app × connection pairs:  {len(blocked_rows)}")
    log(f"  Included app × connection pairs: {len(included_rows)}")

    # ── Penetration rate per app ──────────────────────────────────────────────
    #
    # For each app:
    #   available_network_spend = sum of daily_spend of connections where app IS included
    #   app_available_spend     = sum of app's own spend on those same connections
    #   penetration_rate        = app_available_spend / available_network_spend
    #
    # Then for each blocked connection:
    #   estimated_uplift = conn_daily_spend × penetration_rate

    log("Calculating per-app penetration rates...", "STEP")

    included_df = pd.DataFrame(included_rows)
    if included_df.empty:
        penetration_df = pd.DataFrame(
            columns=["app_id", "app_available_spend", "available_network_spend", "penetration_rate"]
        )
    else:
        penetration_df = (
            included_df
            .groupby("app_id")
            .agg(
                app_available_spend=("app_spend_on_conn", "sum"),
                available_network_spend=("conn_daily_spend", "sum")
            )
            .reset_index()
        )
        penetration_df["penetration_rate"] = (
            penetration_df["app_available_spend"]
            / penetration_df["available_network_spend"]
        ).where(penetration_df["available_network_spend"] > 0, 0).round(6)

    # ── Save app-level penetration summary ───────────────────────────────────

    if not penetration_df.empty:
        pen_out = penetration_df.rename(columns={
            "app_available_spend":      "current_daily_spend",
            "available_network_spend":  "total_available_rtb_spend"
        })[["app_id", "total_available_rtb_spend", "current_daily_spend", "penetration_rate"]]
        pen_out = pen_out.sort_values("total_available_rtb_spend", ascending=False).reset_index(drop=True)

        if "app_name" in smb_df.columns:
            pen_out = pen_out.merge(smb_df[["app_id", "app_name"]], on="app_id", how="left")
            pen_out = pen_out[["app_id", "app_name", "total_available_rtb_spend",
                                "current_daily_spend", "penetration_rate"]]

        pen_path = f"{OUTPUT_DIR}/p4_app_penetration_rates.csv"
        pen_out.to_csv(pen_path, index=False)
        log(f"App penetration summary → {pen_path} ({len(pen_out)} apps)")

    # ── Build final output ────────────────────────────────────────────────────

    log("Building final audit output...", "STEP")

    blocked_df = pd.DataFrame(blocked_rows)
    if blocked_df.empty:
        log("No blocked opportunities found.", "WARN")
    else:
        blocked_df = blocked_df.merge(
            penetration_df[["app_id", "app_available_spend",
                             "available_network_spend", "penetration_rate"]],
            on="app_id",
            how="left"
        )
        blocked_df["penetration_rate"] = blocked_df["penetration_rate"].fillna(0)
        blocked_df["app_available_spend"] = blocked_df["app_available_spend"].fillna(0)
        blocked_df["available_network_spend"] = blocked_df["available_network_spend"].fillna(0)

        blocked_df["estimated_daily_uplift"] = (
            blocked_df["conn_daily_spend"] * blocked_df["penetration_rate"]
        ).round(2)

        # Merge optional app metadata (app_name etc.) from smb_apps.csv
        if "app_name" in smb_df.columns:
            blocked_df = blocked_df.merge(
                smb_df[["app_id", "app_name"]], on="app_id", how="left"
            )

        # Final column order
        front = ["app_id"]
        if "app_name" in blocked_df.columns:
            front.append("app_name")
        cols = front + [
            "rtb_account_id", "rtb_account_name", "rtb_contact_name",
            "rtb_connection_id", "rtb_connection_name",
            "list_type",
            "conn_daily_spend",
            "app_available_spend",
            "available_network_spend",
            "penetration_rate",
            "estimated_daily_uplift"
        ]
        blocked_df = blocked_df[[c for c in cols if c in blocked_df.columns]]
        blocked_df = blocked_df.sort_values(
            "estimated_daily_uplift", ascending=False
        ).reset_index(drop=True)

        output_path = f"{OUTPUT_DIR}/p4_smb_audit.csv"
        blocked_df.to_csv(output_path, index=False)

        log("═" * 55, "STEP")
        log("PART 4 COMPLETE", "STEP")
        log(f"  Blocked opportunities:        {len(blocked_df)}", "STEP")
        log(f"  Unique SMB apps affected:     {blocked_df['app_id'].nunique()}", "STEP")
        log(f"  Unique RTB connections:       {blocked_df['rtb_connection_id'].nunique()}", "STEP")
        log(f"  Est. total daily uplift:      ${blocked_df['estimated_daily_uplift'].sum():,.0f}", "STEP")
        log(f"  Output: {output_path}", "STEP")
        log("═" * 55, "STEP")
