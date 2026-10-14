"""A two-week slice of the orders feed is replayed, like an extract re-run
after a failed load: every order placed from 10 to 24 February 2018 is
appended again with a fresh id, and its payments are appended again keyed to
the new order id, with fresh payment ids.

Every dbt test passes (ids are fresh, every new order has its payments), but
`orders`, `customers.number_of_orders`, lifetime value and the region report
count those orders twice. `daily_revenue` is incremental and never revisits
February, so it disagrees with `orders` for those days.
Run from the repo root: python cases/duplicate_orders/inject.py
Undo: git checkout jaffle_shop/seeds/raw_orders.csv jaffle_shop/seeds/raw_payments.csv
"""
import csv
from pathlib import Path

SEEDS = Path(__file__).parents[2] / "jaffle_shop/seeds"
START, END = "2018-02-10", "2018-02-25"  # replayed slice, END excluded

orders = list(csv.DictReader((SEEDS / "raw_orders.csv").open()))
payments = list(csv.DictReader((SEEDS / "raw_payments.csv").open()))
hit = [r for r in orders if START <= r["ordered_at"] < END]
order_id = max(int(r["id"]) for r in orders) + 1
payment_id = max(int(r["id"]) for r in payments) + 1
new_id = {r["id"]: str(order_id + i) for i, r in enumerate(hit)}
new_orders = [dict(r, id=new_id[r["id"]]) for r in hit]
new_payments = [dict(p, id=str(payment_id + i), order_id=new_id[p["order_id"]])
                for i, p in enumerate([p for p in payments if p["order_id"] in new_id])]

for name, rows, new in [("raw_orders.csv", orders, new_orders), ("raw_payments.csv", payments, new_payments)]:
    with (SEEDS / name).open("a", newline="") as f:
        csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n").writerows(new)
    assert len(list(csv.DictReader((SEEDS / name).open()))) == len(rows) + len(new)

print(f"replayed {len(new_orders)} orders placed {START} to {END} (original ids {hit[0]['id']}-{hit[-1]['id']}, "
      f"new ids {order_id}-{order_id + len(hit) - 1}) with {len(new_payments)} payments, "
      f"{sum(int(p['amount']) for p in new_payments) / 100:.2f} total")
