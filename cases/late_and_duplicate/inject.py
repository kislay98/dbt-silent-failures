"""Two failures in one load: six February orders arrive late with their
payments (a store whose sync was down), and the same load replays every 10th
payment under a fresh id (a retried batch).

`orders` picks up the late orders and double-counts the replayed payments;
`daily_revenue` never sees the late February days. Every dbt test passes.
Run from the repo root: python cases/late_and_duplicate/inject.py
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
new_orders = [{"id": order_id + i, "user_id": c, "ordered_at": t, "status": "completed"} for i, (c, t, _, _) in enumerate(LATE)]
new_payments = [{"id": payment_id + i, "order_id": order_id + i, "payment_method": m, "amount": cents} for i, (_, _, m, cents) in enumerate(LATE)]
dupes = [dict(r, id=str(payment_id + len(LATE) + i)) for i, r in enumerate(payments[::10])]

for name, rows, new in [("raw_orders.csv", orders, new_orders), ("raw_payments.csv", payments, new_payments + dupes)]:
    with (SEEDS / name).open("a", newline="") as f:
        csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n").writerows(new)
    assert len(list(csv.DictReader((SEEDS / name).open()))) == len(rows) + len(new)

print(f"appended {len(new_orders)} February orders (ids {order_id}-{order_id + len(LATE) - 1}, "
      f"{sum(c for *_, c in LATE) / 100:.2f}) with payments, and {len(dupes)} replayed payments "
      f"(ids {dupes[0]['id']}-{dupes[-1]['id']}, {sum(int(d['amount']) for d in dupes) / 100:.2f})")
