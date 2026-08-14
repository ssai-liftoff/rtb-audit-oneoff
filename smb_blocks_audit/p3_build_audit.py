"""
SMB Blocks Audit — Part 3: Build Audit & Calculate Uplift

Produces three audit files — one per block type — each sorted by estimated
daily uplift descending.

Uplift methodology (consistent across all block types):
──────────────────────────────────────────────────────
  pct_of_network   = blocked_entity_daily_spend / total_network_daily_spend
  estimated_uplift = pct_of_network × publisher_entity_daily_spend

Where publisher_entity_daily_spend is:
  - app_daily_spend     for app-level IAB blocks
  - account_daily_spend for account-level IAB blocks, RTB account blocks,
                         and RTB connection blocks

Block types handled:
  1. IAB categories   — account-level + app-level, output in one file with
                        a block_level column ("account" / "app")
  2. RTB accounts     — account-level only
  3. RTB connections  — account-level only

For account-level blocks the audit row represents the entire account
(not expanded per app), since the block applies uniformly to all apps
under that account and the uplift is estimated against account-wide spend.

Inputs:
  output/smb_blocks_audit/p1_smb_blocks.csv
  output/smb_blocks_audit/p2_network_total.csv
  output/smb_blocks_audit/p2_network_iab.csv
  output/smb_blocks_audit/p2_network_rtb_accounts.csv
  output/smb_blocks_audit/p2_network_rtb_connections.csv

Outputs:
  output/smb_blocks_audit/p3_iab_audit.csv
  output/smb_blocks_audit/p3_rtb_accounts_audit.csv
  output/smb_blocks_audit/p3_rtb_connections_audit.csv
"""

import os
import pandas as pd
from datetime import datetime

OUTPUT_DIR = "output/smb_blocks_audit"
os.makedirs(OUTPUT_DIR, exist_ok=True)


def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {"INFO": "✓", "STEP": "──", "WARN": "⚠", "ERROR": "✗"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}", flush=True)


def split_blocks(series):
    """Split comma-separated block strings and strip whitespace."""
    return series.astype(str).str.split(",")


def explode_blocks(df, id_col, block_col, output_col):
    """
    Explode a comma-separated block column into one row per blocked entity.
    Returns only rows where the block value is non-empty.
    """
    out = df.copy()
    out[output_col] = split_blocks(out[block_col])
    out = out.explode(output_col)
    out[output_col] = out[output_col].astype(str).str.strip()
    out = out[out[output_col].notna() & (out[output_col] != "") & (out[output_col] != "nan")].copy()
    return out


def join_network_spend(df, block_col, network_df, network_id_col):
    """
    Left-join df[block_col] → network_df[network_id_col] to bring in
    daily_spend and pct_of_network for each blocked entity.
    Returns df with new columns: entity_network_daily_spend, entity_pct_of_network.
    Unmatched blocks get 0 (entity not spending on network — unlikely but safe).
    """
    network_lookup = (
        network_df[[network_id_col, "daily_spend", "pct_of_network"]]
        .rename(columns={
            network_id_col:   block_col,
            "daily_spend":    "entity_network_daily_spend",
            "pct_of_network": "entity_pct_of_network",
        })
    )
    # Ensure consistent string types for the join key
    df[block_col] = df[block_col].astype(str).str.strip()
    network_lookup[block_col] = network_lookup[block_col].astype(str).str.strip()

    merged = df.merge(network_lookup, on=block_col, how="left")
    merged["entity_network_daily_spend"] = merged["entity_network_daily_spend"].fillna(0)
    merged["entity_pct_of_network"]      = merged["entity_pct_of_network"].fillna(0)
    return merged


if __name__ == "__main__":
    log("═" * 55, "STEP")
    log("SMB BLOCKS AUDIT — PART 3: BUILD AUDIT", "STEP")
    log("═" * 55, "STEP")

    # ── Load inputs ───────────────────────────────────────────────────────────

    for path in [
        f"{OUTPUT_DIR}/p1_smb_blocks.csv",
        f"{OUTPUT_DIR}/p2_network_total.csv",
        f"{OUTPUT_DIR}/p2_network_iab.csv",
        f"{OUTPUT_DIR}/p2_network_rtb_accounts.csv",
        f"{OUTPUT_DIR}/p2_network_rtb_connections.csv",
    ]:
        if not os.path.exists(path):
            raise FileNotFoundError(f"Missing required input: {path} — run previous parts first")

    log("Loading data from parts 1–2...", "STEP")

    smb_df       = pd.read_csv(f"{OUTPUT_DIR}/p1_smb_blocks.csv")
    total_df     = pd.read_csv(f"{OUTPUT_DIR}/p2_network_total.csv")
    iab_df       = pd.read_csv(f"{OUTPUT_DIR}/p2_network_iab.csv")
    rtb_acc_df   = pd.read_csv(f"{OUTPUT_DIR}/p2_network_rtb_accounts.csv")
    rtb_conn_df  = pd.read_csv(f"{OUTPUT_DIR}/p2_network_rtb_connections.csv")

    total_daily_spend = float(total_df["total_daily_spend"].iloc[0])

    for col in ["account_id", "app_id"]:
        smb_df[col] = smb_df[col].astype(str).str.strip()
    smb_df["app_daily_spend"]     = pd.to_numeric(smb_df["app_daily_spend"],     errors="coerce").fillna(0)
    smb_df["account_daily_spend"] = pd.to_numeric(smb_df["account_daily_spend"], errors="coerce").fillna(0)

    log(f"  SMB apps:              {smb_df['app_id'].nunique():,}")
    log(f"  SMB accounts:          {smb_df['account_id'].nunique():,}")
    log(f"  Network daily spend:   ${total_daily_spend:,.0f}")

    # ── 1. IAB Blocks Audit ───────────────────────────────────────────────────
    #
    # Account-level: one row per account × IAB code (use account_daily_spend)
    # App-level:     one row per app × IAB code     (use app_daily_spend)
    # Both in one file with block_level column.

    log("Building IAB audit...", "STEP")

    # -- Account-level IAB --
    acct_has_iab = (
        smb_df["account_blocked_iab"].notna()
        & (smb_df["account_blocked_iab"].astype(str).str.strip() != "")
        & (smb_df["account_blocked_iab"].astype(str).str.strip() != "nan")
    )
    acct_iab_base = (
        smb_df[acct_has_iab]
        .drop_duplicates(subset=["account_id"])
        [["account_id", "account_name", "account_blocked_iab", "account_daily_spend"]]
        .copy()
    )
    if not acct_iab_base.empty:
        acct_iab = explode_blocks(acct_iab_base, "account_id", "account_blocked_iab", "blocked_iab_code")
        acct_iab = join_network_spend(acct_iab, "blocked_iab_code", iab_df, "iab_category_code")
        acct_iab["block_level"]          = "account"
        acct_iab["publisher_daily_spend"] = acct_iab["account_daily_spend"]
        acct_iab["app_id"]               = ""
        acct_iab["app_name"]             = ""
    else:
        acct_iab = pd.DataFrame()
    log(f"  Account-level IAB blocks: {len(acct_iab):,} rows ({acct_iab_base['account_id'].nunique() if not acct_iab_base.empty else 0} accounts)")

    # -- App-level IAB --
    app_has_iab = (
        smb_df["app_blocked_iab"].notna()
        & (smb_df["app_blocked_iab"].astype(str).str.strip() != "")
        & (smb_df["app_blocked_iab"].astype(str).str.strip() != "nan")
    )
    app_iab_base = smb_df[app_has_iab][
        ["account_id", "account_name", "app_id", "app_name", "app_blocked_iab", "app_daily_spend"]
    ].copy()
    if not app_iab_base.empty:
        app_iab = explode_blocks(app_iab_base, "app_id", "app_blocked_iab", "blocked_iab_code")
        app_iab = join_network_spend(app_iab, "blocked_iab_code", iab_df, "iab_category_code")
        app_iab["block_level"]           = "app"
        app_iab["publisher_daily_spend"] = app_iab["app_daily_spend"]
    else:
        app_iab = pd.DataFrame()
    log(f"  App-level IAB blocks:     {len(app_iab):,} rows ({app_iab_base['app_id'].nunique() if not app_iab_base.empty else 0} apps)")

    # -- Combine and calculate uplift --
    iab_cols = [
        "block_level",
        "account_id", "account_name",
        "app_id", "app_name",
        "blocked_iab_code",
        "entity_network_daily_spend",
        "entity_pct_of_network",
        "total_network_daily_spend",
        "publisher_daily_spend",
        "estimated_daily_uplift",
    ]

    iab_parts = [df for df in [acct_iab, app_iab] if not df.empty]
    if iab_parts:
        iab_out = pd.concat(iab_parts, ignore_index=True, sort=False)
        iab_out["total_network_daily_spend"] = total_daily_spend
        iab_out["estimated_daily_uplift"] = (
            iab_out["entity_pct_of_network"] * iab_out["publisher_daily_spend"]
        ).round(2)
        iab_out = iab_out[[c for c in iab_cols if c in iab_out.columns]]
        iab_out = iab_out.sort_values("estimated_daily_uplift", ascending=False).reset_index(drop=True)
        iab_path = f"{OUTPUT_DIR}/p3_iab_audit.csv"
        iab_out.to_csv(iab_path, index=False)
        log(f"  IAB audit → {iab_path} ({len(iab_out):,} rows)")
    else:
        log("  No IAB blocks found — skipping IAB audit output", "WARN")
        iab_out = pd.DataFrame()

    # ── 2. RTB Accounts Audit ─────────────────────────────────────────────────
    #
    # Account-level only. One row per account × blocked RTB account ID.

    log("Building RTB accounts audit...", "STEP")

    acct_has_rtb = (
        smb_df["account_blocked_rtb_accounts"].notna()
        & (smb_df["account_blocked_rtb_accounts"].astype(str).str.strip() != "")
        & (smb_df["account_blocked_rtb_accounts"].astype(str).str.strip() != "nan")
    )
    rtb_acc_base = (
        smb_df[acct_has_rtb]
        .drop_duplicates(subset=["account_id"])
        [["account_id", "account_name", "account_blocked_rtb_accounts", "account_daily_spend"]]
        .copy()
    )
    if not rtb_acc_base.empty:
        rtb_acc_out = explode_blocks(
            rtb_acc_base, "account_id", "account_blocked_rtb_accounts", "blocked_rtb_account_id"
        )
        rtb_acc_out = join_network_spend(
            rtb_acc_out, "blocked_rtb_account_id", rtb_acc_df, "rtb_account_id"
        )
        # Bring in RTB account name from network reference
        rtb_name_lkp = (
            rtb_acc_df[["rtb_account_id", "rtb_account_name"]]
            .drop_duplicates()
            .rename(columns={"rtb_account_id": "blocked_rtb_account_id"})
        )
        rtb_name_lkp["blocked_rtb_account_id"] = rtb_name_lkp["blocked_rtb_account_id"].astype(str).str.strip()
        rtb_acc_out = rtb_acc_out.merge(rtb_name_lkp, on="blocked_rtb_account_id", how="left")
        rtb_acc_out["total_network_daily_spend"] = total_daily_spend
        rtb_acc_out["estimated_daily_uplift"] = (
            rtb_acc_out["entity_pct_of_network"] * rtb_acc_out["account_daily_spend"]
        ).round(2)
        rtb_acc_cols = [
            "account_id", "account_name",
            "blocked_rtb_account_id", "rtb_account_name",
            "entity_network_daily_spend",
            "entity_pct_of_network",
            "total_network_daily_spend",
            "account_daily_spend",
            "estimated_daily_uplift",
        ]
        rtb_acc_out = rtb_acc_out[[c for c in rtb_acc_cols if c in rtb_acc_out.columns]]
        rtb_acc_out = rtb_acc_out.sort_values("estimated_daily_uplift", ascending=False).reset_index(drop=True)
        rtb_acc_path = f"{OUTPUT_DIR}/p3_rtb_accounts_audit.csv"
        rtb_acc_out.to_csv(rtb_acc_path, index=False)
        log(f"  RTB accounts audit → {rtb_acc_path} ({len(rtb_acc_out):,} rows)")
    else:
        log("  No RTB account blocks found — skipping RTB accounts audit output", "WARN")
        rtb_acc_out = pd.DataFrame()

    # ── 3. RTB Connections Audit ──────────────────────────────────────────────
    #
    # Account-level only. One row per account × blocked RTB connection ID.

    log("Building RTB connections audit...", "STEP")

    acct_has_conn = (
        smb_df["account_blocked_rtb_connections"].notna()
        & (smb_df["account_blocked_rtb_connections"].astype(str).str.strip() != "")
        & (smb_df["account_blocked_rtb_connections"].astype(str).str.strip() != "nan")
    )
    rtb_conn_base = (
        smb_df[acct_has_conn]
        .drop_duplicates(subset=["account_id"])
        [["account_id", "account_name", "account_blocked_rtb_connections", "account_daily_spend"]]
        .copy()
    )
    if not rtb_conn_base.empty:
        rtb_conn_out = explode_blocks(
            rtb_conn_base, "account_id", "account_blocked_rtb_connections", "blocked_rtb_connection_id"
        )
        rtb_conn_out = join_network_spend(
            rtb_conn_out, "blocked_rtb_connection_id", rtb_conn_df, "rtb_connection_id"
        )
        conn_name_lkp = (
            rtb_conn_df[["rtb_connection_id", "rtb_connection_name"]]
            .drop_duplicates()
            .rename(columns={"rtb_connection_id": "blocked_rtb_connection_id"})
        )
        conn_name_lkp["blocked_rtb_connection_id"] = conn_name_lkp["blocked_rtb_connection_id"].astype(str).str.strip()
        rtb_conn_out = rtb_conn_out.merge(conn_name_lkp, on="blocked_rtb_connection_id", how="left")
        rtb_conn_out["total_network_daily_spend"] = total_daily_spend
        rtb_conn_out["estimated_daily_uplift"] = (
            rtb_conn_out["entity_pct_of_network"] * rtb_conn_out["account_daily_spend"]
        ).round(2)
        rtb_conn_cols = [
            "account_id", "account_name",
            "blocked_rtb_connection_id", "rtb_connection_name",
            "entity_network_daily_spend",
            "entity_pct_of_network",
            "total_network_daily_spend",
            "account_daily_spend",
            "estimated_daily_uplift",
        ]
        rtb_conn_out = rtb_conn_out[[c for c in rtb_conn_cols if c in rtb_conn_out.columns]]
        rtb_conn_out = rtb_conn_out.sort_values("estimated_daily_uplift", ascending=False).reset_index(drop=True)
        rtb_conn_path = f"{OUTPUT_DIR}/p3_rtb_connections_audit.csv"
        rtb_conn_out.to_csv(rtb_conn_path, index=False)
        log(f"  RTB connections audit → {rtb_conn_path} ({len(rtb_conn_out):,} rows)")
    else:
        log("  No RTB connection blocks found — skipping RTB connections audit output", "WARN")
        rtb_conn_out = pd.DataFrame()

    # ── Final summary ─────────────────────────────────────────────────────────

    log("═" * 55, "STEP")
    log("PART 3 COMPLETE", "STEP")

    if not iab_out.empty:
        log(f"  IAB audit rows:           {len(iab_out):,}", "STEP")
        log(f"    Account-level blocks:   {(iab_out['block_level'] == 'account').sum():,}", "STEP")
        log(f"    App-level blocks:       {(iab_out['block_level'] == 'app').sum():,}", "STEP")
        log(f"    Est. total daily uplift:${iab_out['estimated_daily_uplift'].sum():,.0f}", "STEP")

    if not rtb_acc_out.empty:
        log(f"  RTB acct audit rows:      {len(rtb_acc_out):,}", "STEP")
        log(f"    Unique accounts:        {rtb_acc_out['account_id'].nunique():,}", "STEP")
        log(f"    Est. total daily uplift:${rtb_acc_out['estimated_daily_uplift'].sum():,.0f}", "STEP")

    if not rtb_conn_out.empty:
        log(f"  RTB conn audit rows:      {len(rtb_conn_out):,}", "STEP")
        log(f"    Unique accounts:        {rtb_conn_out['account_id'].nunique():,}", "STEP")
        log(f"    Est. total daily uplift:${rtb_conn_out['estimated_daily_uplift'].sum():,.0f}", "STEP")

    log("═" * 55, "STEP")
