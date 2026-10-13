"""Six February orders and their payments arrive in the April load, like a
store whose sync was down for a month and backfilled with fresh ids.

`orders`, `customers` and `region_revenue` read the seeds and pick them up.
`daily_revenue` is incremental and only rebuilds the last three days, so the
six February days never get them. Every dbt test passes while the daily
dashboard and the orders table disagree about February.
Run from the repo root: python cases/late_orders/inject.py
Undo: git checkout jaffle_shop/seeds/raw_orders.csv jaffle_shop/seeds/raw_payments.csv
"""
import csv
from pathlib import Path

SEEDS = Path(__file__).parents[2] / "jaffle_shop/seeds"
LATE = [  # (customer, ordered_at, payment_method, cents)
    (2, "2018-02-03 10:12:44", "credit_card", 1200),
    (15, "2018-02-08 19:30:05", "bank_transfer", 900),
    (28, "2018-02-13 13:05:51", "credit_card", 1500),
    (41, "2018-02-17 09:48:19", "coupon", 600),
    (60, "2018-02-22 21:17:33", "credit_card", 2000),
    (77, "2018-02-26 15:59:02", "bank_transfer", 300),
]

orders = list(csv.DictReader((SEEDS / "raw_orders.csv").open()))
payments = list(csv.DictReader((SEEDS / "raw_payments.csv").open()))
order_id = max(int(r["id"]) for r in orders) + 1
payment_id = max(int(r["id"]) for r in payments) + 1
new_orders, new_payments = [], []
for i, (customer, ordered_at, method, cents) in enumerate(LATE):
    new_orders.append({"id": order_id + i, "user_id": customer, "ordered_at": ordered_at, "status": "completed"})
    new_payments.append({"id": payment_id + i, "order_id": order_id + i, "payment_method": method, "amount": cents})

for name, rows, new in [("raw_orders.csv", orders, new_orders), ("raw_payments.csv", payments, new_payments)]:
    with (SEEDS / name).open("a", newline="") as f:
        csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n").writerows(new)
    assert len(list(csv.DictReader((SEEDS / name).open()))) == len(rows) + len(new)

print(f"appended {len(new_orders)} February orders (ids {order_id}-{order_id + len(LATE) - 1}, "
      f"{sum(c for *_, c in LATE) / 100:.2f} total) and their payments")
