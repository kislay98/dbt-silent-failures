"""Same failure as region_fanout, quieter: only the two biggest customers'
region rows are re-sent with a later updated_at and the same region, so the
regions seed grows from 100 rows to 102 and the build log barely moves.

`region_revenue` joins regions before it aggregates, so those two customers'
payments are counted twice. Every dbt test passes.
Run from the repo root: python cases/region_fanout_quiet/inject.py
Undo: git checkout jaffle_shop/seeds/raw_customer_regions.csv
"""
import csv
from collections import defaultdict
from pathlib import Path

SEEDS = Path(__file__).parents[2] / "jaffle_shop/seeds"
REGIONS = SEEDS / "raw_customer_regions.csv"

customer_of = {r["id"]: r["user_id"] for r in csv.DictReader((SEEDS / "raw_orders.csv").open())}
paid = defaultdict(int)
for p in csv.DictReader((SEEDS / "raw_payments.csv").open()):
    paid[customer_of[p["order_id"]]] += int(p["amount"])
hit = set(sorted(paid, key=paid.get, reverse=True)[:2])
rows = list(csv.DictReader(REGIONS.open()))
resent = [dict(r, updated_at="2018-04-02") for r in rows if r["customer_id"] in hit]
assert len(resent) == 2

with REGIONS.open("a", newline="") as f:
    csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n").writerows(resent)

assert len(list(csv.DictReader(REGIONS.open()))) == len(rows) + 2
print(f"re-sent region rows for customers {sorted(hit, key=int)} "
      f"({', '.join(f'{paid[c] / 100:.2f}' for c in sorted(hit, key=int))} lifetime payments)")
