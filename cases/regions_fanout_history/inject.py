"""The regions feed starts carrying history: on 1 March 2018 the six customers
with the most orders move to another region, and the feed appends a second
row for each with the new region and a later `updated_at` instead of updating
the old one.

`region_revenue` joins the feed as if it had one row per customer, so those
six customers, their orders and their payments are counted in both regions.
Every dbt test passes: the feed is tested `not_null`, not `unique`.
Finance reports revenue by the customer's current region, so the correct
output is the latest row per customer.
Run from the repo root: python cases/regions_fanout_history/inject.py
Undo: git checkout jaffle_shop/seeds/raw_customer_regions.csv
"""
import csv
from collections import Counter
from pathlib import Path

SEEDS = Path(__file__).parents[2] / "jaffle_shop/seeds"
REGIONS = SEEDS / "raw_customer_regions.csv"
MOVED_AT = "2018-03-01"

orders = Counter(r["user_id"] for r in csv.DictReader((SEEDS / "raw_orders.csv").open()))
rows = list(csv.DictReader(REGIONS.open()))
assert max(r["updated_at"] for r in rows) < MOVED_AT
region_of = {r["customer_id"]: r["region"] for r in rows}
names = sorted(set(region_of.values()))
hit = sorted(orders, key=lambda c: (-orders[c], int(c)))[:6]
moved = [{"customer_id": c, "region": names[(names.index(region_of[c]) + 1) % len(names)], "updated_at": MOVED_AT}
         for c in hit]

with REGIONS.open("a", newline="") as f:
    csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n").writerows(moved)

assert len(list(csv.DictReader(REGIONS.open()))) == len(rows) + len(moved)
print(f"moved {len(moved)} customers on {MOVED_AT}: "
      + ", ".join(f"{m['customer_id']} {region_of[m['customer_id']]} to {m['region']}" for m in sorted(moved, key=lambda m: int(m["customer_id"]))))
