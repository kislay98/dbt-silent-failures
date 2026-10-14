"""From 10 March 2018 a new fulfilment flow writes `delivered` where the app
used to write `completed`. The `accepted_values` tests on status failed on the
first run and the on-call added the new value to the lists in both schema
files to stop the alert, so the next run was green.

`customers.completed_lifetime_value` still filters on `status = 'completed'`,
so every delivered order silently drops out of it: finance's completed revenue
for March falls while total revenue is unchanged.
Run from the repo root: python cases/status_new_value/inject.py
Undo: git checkout jaffle_shop/seeds/raw_orders.csv jaffle_shop/models/schema.yml jaffle_shop/models/staging/schema.yml
"""
import csv
from pathlib import Path

SHOP = Path(__file__).parents[2] / "jaffle_shop"
SEED = SHOP / "seeds/raw_orders.csv"
SCHEMAS = [SHOP / "models/schema.yml", SHOP / "models/staging/schema.yml"]
CUTOVER = "2018-03-10"
OLD = "values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']"
NEW = OLD[:-1] + ", 'delivered']"

rows = list(csv.DictReader(SEED.open()))
hit = [r for r in rows if r["ordered_at"] >= CUTOVER and r["status"] == "completed"]
for r in hit:
    r["status"] = "delivered"

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

for p in SCHEMAS:
    s = p.read_text()
    assert s.count(OLD) == 1, p
    p.write_text(s.replace(OLD, NEW))

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"wrote delivered for {len(hit)} completed orders from {CUTOVER} on (ids {hit[0]['id']}-{hit[-1]['id']}), "
      f"widened accepted_values in {len(SCHEMAS)} schema files")
