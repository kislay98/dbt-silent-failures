"""Re-send 8 customers' region rows with a later updated_at, like a dimension
feed that replays changed records instead of replacing them.

Each re-sent row keeps the same region, so the regions table looks right by
eye and every dbt test still passes (nothing tests the key for uniqueness).
`region_revenue` joins regions before it aggregates, so those customers'
payments are counted twice: revenue inflates while the distinct customer and
order counts stay correct.
Run from the repo root: python cases/region_fanout/inject.py
Undo: git checkout jaffle_shop/seeds/raw_customer_regions.csv
"""
import csv
from pathlib import Path

SEEDS = Path(__file__).parents[2] / "jaffle_shop/seeds"
REGIONS = SEEDS / "raw_customer_regions.csv"

with_orders = sorted({int(r["user_id"]) for r in csv.DictReader((SEEDS / "raw_orders.csv").open())})
hit = set(with_orders[::8])
rows = list(csv.DictReader(REGIONS.open()))
resent = [dict(r, updated_at="2018-04-02") for r in rows if int(r["customer_id"]) in hit]
assert len(resent) == len(hit), "every chosen customer needs a region row"

with REGIONS.open("a", newline="") as f:
    csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n").writerows(resent)

assert len(list(csv.DictReader(REGIONS.open()))) == len(rows) + len(resent)
print(f"re-sent {len(resent)} region rows for customers {sorted(hit)}")
