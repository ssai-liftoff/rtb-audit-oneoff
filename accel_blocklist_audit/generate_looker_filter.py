"""
Generate Looker Frontend Filter Expression

Reads unblocked_combos.csv and outputs a Looker custom filter expression
with one (source_app AND entity) condition per row, all joined with OR.

Field names used (accelerate_analytics / accelerate_spot explore):
  source app  → revenue_summary.source_app_app_store_id
  campaign    → revenue_summary.campaign_id
  customer    → revenue_summary.customer_id
  adv. app    → revenue_summary.dest_app_id
  global      → source app only (no entity dimension)

Output: output/accel_blocklist_audit/looker_filter_expression.txt
"""

import pandas as pd

COMBOS_FILE = "output/accel_blocklist_audit/unblocked_combos.csv"
OUTPUT_FILE = "output/accel_blocklist_audit/looker_filter_expression.txt"

LEVEL_FIELD = {
    "campaign":       "revenue_summary.campaign_id",
    "customer":       "revenue_summary.customer_id",
    "advertiser_app": "revenue_summary.dest_app_id",
}

SOURCE_APP_FIELD = "revenue_summary.source_app_app_store_id"

combos = pd.read_csv(COMBOS_FILE, dtype=str)
combos.columns = ["blocklist_id", "source_app_id", "block_level", "entity_id"]
combos["source_app_id"] = combos["source_app_id"].str.strip()
combos["entity_id"]     = combos["entity_id"].fillna("").str.strip()
combos["block_level"]   = combos["block_level"].str.strip()

lines = []
for _, row in combos.iterrows():
    level      = row["block_level"]
    source_app = row["source_app_id"]
    entity_id  = row["entity_id"]

    source_cond = f'${{{SOURCE_APP_FIELD}}} = "{source_app}"'

    if level == "global" or not entity_id:
        lines.append(f"({source_cond})")
    else:
        entity_field = LEVEL_FIELD.get(level)
        if entity_field:
            entity_cond = f'${{{entity_field}}} = {entity_id}'
            lines.append(f"({source_cond} AND {entity_cond})")
        else:
            lines.append(f"({source_cond})")

expression = " OR\n".join(lines)

with open(OUTPUT_FILE, "w") as f:
    f.write(expression)

print(f"Written {len(lines)} conditions to {OUTPUT_FILE}")
print(f"Total characters: {len(expression):,}")
print("\nFirst 3 lines preview:")
for line in lines[:3]:
    print(" ", line)
