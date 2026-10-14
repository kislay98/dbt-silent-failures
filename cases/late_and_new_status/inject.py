"""Two things land in the April load. Six February orders and their payments
arrive a month late with fresh ids, from a store whose sync was down; and
from 10 March the new fulfilment flow writes `delivered` where the app used
to write `completed`, for the late orders too. The `accepted_values` tests
on status failed on the first run and the on-call added the new value to
both schema files to stop the alert.

`daily_revenue` is incremental and only rebuilds the last three days, so
the six February days never get the late orders while `orders` has them.
`customers.completed_lifetime_value` filters on `status = 'completed'`, so
every delivered order drops out of it, the late six included. Every dbt test
passes.
Run from the repo root: python cases/late_and_new_status/inject.py
Undo: git checkout jaffle_shop/seeds/raw_orders.csv jaffle_shop/seeds/raw_payments.csv jaffle_shop/models/schema.yml jaffle_shop/models/staging/schema.yml
"""
import csv
from pathlib import Path

SHOP = Path(__file__).parents[2] / "jaffle_shop"
SEEDS = SHOP / "seeds"
SCHEMAS = [SHOP / "models/schema.yml", SHOP / "models/staging/schema.yml"]
CUTOVER = "2018-03-10"
OLD = "values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']"
NEW = OLD[:-1] + ", 'delivered']"
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
hit = [r for r in orders if r["ordered_at"] >= CUTOVER and r["status"] == "completed"]
for r in hit:
    r["status"] = "delivered"
order_id = max(int(r["id"]) for r in orders) + 1
payment_id = max(int(r["id"]) for r in payments) + 1
new_orders = [{"id": order_id + i, "user_id": c, "ordered_at": t, "status": "delivered"} for i, (c, t, _, _) in enumerate(LATE)]
new_payments = [{"id": payment_id + i, "order_id": order_id + i, "payment_method": m, "amount": cents} for i, (_, _, m, cents) in enumerate(LATE)]

for name, rows, new in [("raw_orders.csv", orders, new_orders), ("raw_payments.csv", payments, new_payments)]:
    with (SEEDS / name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
        w.writeheader()
        w.writerows(rows + new)
    assert len(list(csv.DictReader((SEEDS / name).open()))) == len(rows) + len(new)
for p in SCHEMAS:
    s = p.read_text()
    assert s.count(OLD) == 1, p
    p.write_text(s.replace(OLD, NEW))

print(f"wrote delivered for {len(hit)} completed orders from {CUTOVER} on (ids {hit[0]['id']}-{hit[-1]['id']}); appended "
      f"{len(LATE)} February orders as delivered (ids {order_id}-{order_id + len(LATE) - 1}, {sum(c for *_, c in LATE) / 100:.2f}) "
      f"and their payments; widened accepted_values in {len(SCHEMAS)} schema files")
