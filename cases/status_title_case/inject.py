"""From 2018-02-15 a new app release writes order statuses in title case
(`Completed`, `Placed`, `Shipped`, ...). The `accepted_values` tests on status
failed on the first run and the on-call widened the lists in both schema
files to stop the alert, so the next run was green.

`customers.completed_lifetime_value` still filters on `status = 'completed'`,
so every completed order from the cut-over on silently drops out of it.
Run from the repo root: python cases/status_title_case/inject.py
Undo: git checkout jaffle_shop/seeds/raw_orders.csv jaffle_shop/models/schema.yml jaffle_shop/models/staging/schema.yml
"""
import csv
from pathlib import Path

SHOP = Path(__file__).parents[2] / "jaffle_shop"
SEED = SHOP / "seeds/raw_orders.csv"
SCHEMAS = [SHOP / "models/schema.yml", SHOP / "models/staging/schema.yml"]
CUTOVER = "2018-02-15"
OLD = "values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']"
NEW = OLD[:-1] + ", 'Placed', 'Shipped', 'Completed', 'Return_pending', 'Returned']"

rows = list(csv.DictReader(SEED.open()))
hit = [r for r in rows if r["ordered_at"] >= CUTOVER]
for r in hit:
    r["status"] = r["status"].capitalize()

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

for p in SCHEMAS:
    s = p.read_text()
    assert s.count(OLD) == 1, p
    p.write_text(s.replace(OLD, NEW))

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"title-cased status for {len(hit)} orders from {CUTOVER} on (ids {hit[0]['id']}-{hit[-1]['id']}), "
      f"widened accepted_values in {len(SCHEMAS)} schema files")
