"""
Low Share of Wallet Analysis - Part 4: Final Output
Reads p3_creative_split.csv, renames columns, reorders and saves final CSV.
Output: output/low_sov_analysis/low_sov_final.csv
"""

import os
import pandas as pd

OUTPUT_DIR = "output/low_sov_analysis"

def main():
    p3_path = f"{OUTPUT_DIR}/p3_creative_split.csv"
    if not os.path.exists(p3_path):
        raise FileNotFoundError(f"Part 3 output not found: {p3_path} - run Part 3 first")

    print("Loading Part 3 output...")
    df = pd.read_csv(p3_path)
    print(f"  {len(df)} apps, {len(df.columns)} columns loaded")

    rename_map = {
        "market_id": "Market ID", "app_name": "App Name", "account_name": "Account Name",
        "am_name": "AM Name", "from_previous_analysis": "From Previous Analysis?",
        "total_spend_90d": "Total Spend (90D)", "vx_spend_90d": "VX Spend (90D)",
        "non_vx_spend_90d": "Non-VX Spend (90D)", "vx_spend_pct_90d": "VX Spend % (90D)",
        "vx_rank_90d": "VX Rank (90D)", "exchanges_above_vx_90d": "Exchanges Above VX (90D)",
        "vx_video_spend_90d": "VX Video Spend (90D)", "vx_video_pct_90d": "VX Video % (90D)",
        "vx_non_video_spend_90d": "VX Non-Video Spend (90D)", "vx_non_video_pct_90d": "VX Non-Video % (90D)",
        "vx_video_rank_90d": "VX Video Rank (90D)", "exchanges_above_vx_video_90d": "Exchanges Above VX - Video (90D)",
        "vx_non_video_rank_90d": "VX Non-Video Rank (90D)", "exchanges_above_vx_non_video_90d": "Exchanges Above VX - Non-Video (90D)",
        "non_vx_video_spend_90d": "Non-VX Video Spend (90D)", "non_vx_video_pct_90d": "Non-VX Video % (90D)",
        "non_vx_non_video_spend_90d": "Non-VX Non-Video Spend (90D)", "non_vx_non_video_pct_90d": "Non-VX Non-Video % (90D)",
        "total_spend_30d": "Total Spend (30D)", "vx_spend_30d": "VX Spend (30D)",
        "non_vx_spend_30d": "Non-VX Spend (30D)", "vx_spend_pct_30d": "VX Spend % (30D)",
        "vx_rank_30d": "VX Rank (30D)", "exchanges_above_vx_30d": "Exchanges Above VX (30D)",
        "won_or_lost_rank_30d": "Won or Lost Rank (30D)?",
        "vx_video_spend_30d": "VX Video Spend (30D)", "vx_video_pct_30d": "VX Video % (30D)",
        "vx_non_video_spend_30d": "VX Non-Video Spend (30D)", "vx_non_video_pct_30d": "VX Non-Video % (30D)",
        "vx_video_rank_30d": "VX Video Rank (30D)", "exchanges_above_vx_video_30d": "Exchanges Above VX - Video (30D)",
        "vx_non_video_rank_30d": "VX Non-Video Rank (30D)", "exchanges_above_vx_non_video_30d": "Exchanges Above VX - Non-Video (30D)",
        "non_vx_video_spend_30d": "Non-VX Video Spend (30D)", "non_vx_video_pct_30d": "Non-VX Video % (30D)",
        "non_vx_non_video_spend_30d": "Non-VX Non-Video Spend (30D)", "non_vx_non_video_pct_30d": "Non-VX Non-Video % (30D)",
    }
    df = df.rename(columns=rename_map)

    col_order = [
        "Market ID", "App Name", "Account Name", "AM Name", "From Previous Analysis?",
        "Total Spend (90D)", "VX Spend (90D)", "Non-VX Spend (90D)", "VX Spend % (90D)",
        "VX Rank (90D)", "Exchanges Above VX (90D)",
        "VX Video Spend (90D)", "VX Video % (90D)", "VX Non-Video Spend (90D)", "VX Non-Video % (90D)",
        "VX Video Rank (90D)", "Exchanges Above VX - Video (90D)",
        "VX Non-Video Rank (90D)", "Exchanges Above VX - Non-Video (90D)",
        "Non-VX Video Spend (90D)", "Non-VX Video % (90D)",
        "Non-VX Non-Video Spend (90D)", "Non-VX Non-Video % (90D)",
        "Total Spend (30D)", "VX Spend (30D)", "Non-VX Spend (30D)", "VX Spend % (30D)",
        "VX Rank (30D)", "Exchanges Above VX (30D)", "Won or Lost Rank (30D)?",
        "VX Video Spend (30D)", "VX Video % (30D)", "VX Non-Video Spend (30D)", "VX Non-Video % (30D)",
        "VX Video Rank (30D)", "Exchanges Above VX - Video (30D)",
        "VX Non-Video Rank (30D)", "Exchanges Above VX - Non-Video (30D)",
        "Non-VX Video Spend (30D)", "Non-VX Video % (30D)",
        "Non-VX Non-Video Spend (30D)", "Non-VX Non-Video % (30D)",
    ]
    col_order = [c for c in col_order if c in df.columns]
    pct_cols = [c for c in df.columns if "%" in c]
    for col in pct_cols:
        df[col] = (df[col] / 100).round(4)

    from datetime import datetime
    date_str = datetime.now().strftime("%Y%m%d")
    output_path = f"{OUTPUT_DIR}/low_sov_final_{date_str}.csv"
    df.to_csv(output_path, index=False)
    print(f"Done! {len(df)} apps, {len(df.columns)} columns -> {output_path}")

main()