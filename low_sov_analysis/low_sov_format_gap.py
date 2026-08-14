"""
Low Share of Wallet — Ad Format Gap (89 apps, from cached data)

Uses raw_p5_exchange.csv (already fetched) to find market_id × ad_format combos
where VX spend = 0 and Non-VX spend > 0, for the 89 curated apps.

No Looker call needed — runs entirely from cache.

Output: output/low_sov_analysis/format_gap_audit.csv
  Columns: market_id, ad_format, non_vx_spend_30d, vx_spend_30d
"""

import os
import pandas as pd

OUTPUT_DIR = "output/low_sov_analysis"
CACHE_PATH = f"{OUTPUT_DIR}/raw_p5_exchange.csv"
OUTPUT_PATH = f"{OUTPUT_DIR}/format_gap_audit.csv"

VUNGLE_EXCHANGE = "VUNGLE"


def main():
    if not os.path.exists(CACHE_PATH):
        raise FileNotFoundError(
            f"Cache not found: {CACHE_PATH} — run low_sov_part5_deep_dive.py first"
        )

    print(f"Loading {CACHE_PATH}...")
    df = pd.read_csv(CACHE_PATH, dtype=str).fillna("")
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce").fillna(0)
    df["ad_format"] = df["ad_format"].str.lower().str.strip()
    df = df[(df["market_id"] != "") & (df["ad_format"] != "") & (df["exchange"] != "")].copy()

    print(f"  {len(df):,} rows, {df['market_id'].nunique()} apps, "
          f"formats: {sorted(df['ad_format'].unique())}")

    # Aggregate VX and Non-VX spend per market_id × ad_format
    dims = ["market_id", "ad_format"]

    vx = (
        df[df["exchange"].str.upper() == VUNGLE_EXCHANGE]
        .groupby(dims)["revenue"].sum().reset_index()
        .rename(columns={"revenue": "vx_spend_30d"})
    )
    nvx = (
        df[df["exchange"].str.upper() != VUNGLE_EXCHANGE]
        .groupby(dims)["revenue"].sum().reset_index()
        .rename(columns={"revenue": "non_vx_spend_30d"})
    )

    # All app × format combos present in data
    all_combos = df[dims].drop_duplicates()
    merged = (
        all_combos
        .merge(nvx, on=dims, how="left")
        .merge(vx, on=dims, how="left")
    )
    merged["vx_spend_30d"] = merged["vx_spend_30d"].fillna(0)
    merged["non_vx_spend_30d"] = merged["non_vx_spend_30d"].fillna(0)

    # Filter: Non-VX > 0 AND VX = 0
    gap = merged[
        (merged["non_vx_spend_30d"] > 0) &
        (merged["vx_spend_30d"] == 0)
    ].copy()

    gap = gap.sort_values(["market_id", "non_vx_spend_30d"], ascending=[True, False])
    gap = gap[["market_id", "ad_format", "non_vx_spend_30d", "vx_spend_30d"]].reset_index(drop=True)

    print(f"\nResults:")
    print(f"  Total app × format combos: {len(merged)}")
    print(f"  Gap combos (Non-VX > 0, VX = 0): {len(gap)}")
    print(f"  Apps with at least one format gap: {gap['market_id'].nunique()}")
    print(f"\nFormat breakdown of gaps:")
    print(gap.groupby("ad_format")["non_vx_spend_30d"].agg(["count", "sum"])
          .rename(columns={"count": "gap_combos", "sum": "total_non_vx_spend"})
          .sort_values("total_non_vx_spend", ascending=False)
          .to_string())

    gap.to_csv(OUTPUT_PATH, index=False)
    print(f"\nSaved → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
