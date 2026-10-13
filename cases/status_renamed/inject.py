"""Rename the orders feed's status column going forward: from 2018-03-01 the
feed writes `order_status` and leaves `status` empty. `order_status` is
backfilled for every row, as a migrated feed would do.

`stg_orders` still reads `status`, so it is NULL for every order from the
cut-over on. `accepted_values` ignores NULL and nothing tests `not_null` on
status, so every dbt test passes, while `customers.completed_lifetime_value`
silently stops counting those orders.
Run from the repo root: python cases/status_renamed/inject.py
Rebuild with `dbt seed --full-refresh` then `dbt build`: the seed table gains a column.
Undo: git checkout jaffle_shop/seeds/raw_orders.csv
"""
import csv
from pathlib import Path

SEED = Path(__file__).parents[2] / "jaffle_shop/seeds/raw_orders.csv"
CUTOVER = "2018-03-01"

rows = list(csv.DictReader(SEED.open()))
for r in rows:
    r["order_status"] = r["status"]
    if r["ordered_at"] >= CUTOVER:
        r["status"] = ""
hit = [r for r in rows if r["status"] == ""]

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=[*rows[0].keys()], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"blanked status for {len(hit)} orders from {CUTOVER} on (ids {hit[0]['id']}-{hit[-1]['id']}), "
      f"added order_status for all {len(rows)}")
