"""The orders feed drops the timestamp on one week of orders: every order
placed from 10 to 16 March 2018 arrives with an empty `ordered_at`, like an
export whose date column failed to parse for one batch.

`stg_orders` casts it to `order_date`, so those orders carry a NULL date
through `orders` and into `customers.first_order` and `most_recent_order`.
Nothing tests `not_null` on the date, and `daily_revenue` never sees the rows
(its incremental filter drops a NULL date), so every dbt test passes while
March revenue by order date in `orders` is short and the dashboard, built
before the batch, still shows the old March.
Run from the repo root: python cases/null_order_dates/inject.py
Undo: git checkout jaffle_shop/seeds/raw_orders.csv
"""
import csv
from pathlib import Path

SEED = Path(__file__).parents[2] / "jaffle_shop/seeds/raw_orders.csv"
START, END = "2018-03-10", "2018-03-17"  # END excluded

rows = list(csv.DictReader(SEED.open()))
hit = [r for r in rows if START <= r["ordered_at"] < END]
for r in hit:
    r["ordered_at"] = ""

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"blanked ordered_at for {len(hit)} orders placed {START} to {END} (ids {hit[0]['id']}-{hit[-1]['id']})")
