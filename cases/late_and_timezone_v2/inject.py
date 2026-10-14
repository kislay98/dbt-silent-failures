"""Six February orders and their payments arrive in the April load from a
store whose sync was down for a month, with fresh ids, as in `late_orders`;
the store's new sync writes `ordered_at` in UTC where the shop's feed is in
local time (UTC+05:30), so the three orders placed before 05:30 local land
a day early, one of them in January.

`orders`, `customers` and `region_revenue` pick the six up. `daily_revenue`
is incremental and only rebuilds the last three days, so February never gets
them. Every dbt test passes while the daily dashboard and the orders table
disagree about February, and the orders table disagrees with the store about
three dates.
Run from the repo root: python cases/late_and_timezone/inject.py
Undo: git checkout jaffle_shop/seeds/raw_orders.csv jaffle_shop/seeds/raw_payments.csv
"""
import csv
from datetime import datetime, timedelta
from pathlib import Path

SEEDS = Path(__file__).parents[2] / "jaffle_shop/seeds"
SHIFT = timedelta(hours=5, minutes=30)
LATE = [  # (customer, ordered_at as the store shows it, local time; payment_method, cents)
    (41, "2018-02-01 04:48:19", "coupon", 600),
    (2, "2018-02-03 02:12:44", "credit_card", 1200),
    (15, "2018-02-08 19:30:05", "bank_transfer", 900),
    (28, "2018-02-13 13:05:51", "credit_card", 1500),
    (60, "2018-02-22 21:17:33", "credit_card", 2000),
    (77, "2018-02-26 00:59:02", "bank_transfer", 300),
]

orders = list(csv.DictReader((SEEDS / "raw_orders.csv").open()))
payments = list(csv.DictReader((SEEDS / "raw_payments.csv").open()))
order_id = max(int(r["id"]) for r in orders) + 1
payment_id = max(int(r["id"]) for r in payments) + 1
new_orders, new_payments, moved = [], [], []
for i, (customer, local, method, cents) in enumerate(LATE):
    utc = datetime.fromisoformat(local) - SHIFT
    if utc.date() != datetime.fromisoformat(local).date():
        moved.append(order_id + i)
    new_orders.append({"id": order_id + i, "user_id": customer, "ordered_at": utc.strftime("%Y-%m-%d %H:%M:%S"), "status": "completed"})
    new_payments.append({"id": payment_id + i, "order_id": order_id + i, "payment_method": method, "amount": cents})

for name, rows, new in [("raw_orders.csv", orders, new_orders), ("raw_payments.csv", payments, new_payments)]:
    with (SEEDS / name).open("a", newline="") as f:
        csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n").writerows(new)
    assert len(list(csv.DictReader((SEEDS / name).open()))) == len(rows) + len(new)

print(f"appended {len(new_orders)} February orders (ids {order_id}-{order_id + len(LATE) - 1}, "
      f"{sum(c for *_, c in LATE) / 100:.2f} total) and their payments, timestamps 5:30 early; "
      f"{len(moved)} land a day early: ids {moved}")
