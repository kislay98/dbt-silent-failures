"""Two changes to the orders feed in one migration. From 1 March 2018 the feed
writes `order_status` and leaves `status` empty (`order_status` backfilled
for every row); and from 10 March a new fulfilment flow writes `delivered`
where the app used to write `completed`, in the new column only.

`stg_orders` still reads `status`, so it is NULL for every order from
1 March and `customers.completed_lifetime_value` stops counting them. The
`delivered` value never reaches a test: `accepted_values` ignores NULL and
nobody reads `order_status`, so no schema file was widened. Reading the new
column alone brings the completed orders back but leaves the delivered ones
out, and makes `accepted_values` fail. Every dbt test passes as shipped.
Run from the repo root: python cases/rename_and_new_status/inject.py
Rebuild with `dbt seed --full-refresh` then `dbt build`: the seed table gains a column.
Undo: git checkout jaffle_shop/seeds/raw_orders.csv
"""
import csv
from pathlib import Path

SEED = Path(__file__).parents[2] / "jaffle_shop/seeds/raw_orders.csv"
RENAMED_FROM, DELIVERED_FROM = "2018-03-01", "2018-03-10"

rows = list(csv.DictReader(SEED.open()))
for r in rows:
    r["order_status"] = r["status"]
    if r["ordered_at"] >= RENAMED_FROM:
        r["status"] = ""
    if r["ordered_at"] >= DELIVERED_FROM and r["order_status"] == "completed":
        r["order_status"] = "delivered"
blanked = [r for r in rows if r["status"] == ""]
delivered = [r for r in rows if r["order_status"] == "delivered"]

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=[*rows[0].keys()], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"blanked status for {len(blanked)} orders from {RENAMED_FROM} (ids {blanked[0]['id']}-{blanked[-1]['id']}), added order_status "
      f"for all {len(rows)}; {len(delivered)} completed orders from {DELIVERED_FROM} carry delivered (ids {[r['id'] for r in delivered]})")
