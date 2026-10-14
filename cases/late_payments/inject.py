"""Eight February orders were paid in two instalments and the second leg
cleared late: the payment rows arrive in the April load with fresh ids, like a
bank transfer feed that posts settlements weeks after the order.

`orders`, `customers` and `region_revenue` read the payments seed and pick
them up. `daily_revenue` is incremental and only rebuilds the last three days,
so the eight February days keep their old revenue. Every dbt test passes while
the daily dashboard and the orders table disagree about February revenue, with
the order counts per day still agreeing.
Run from the repo root: python cases/late_payments/inject.py
Undo: git checkout jaffle_shop/seeds/raw_payments.csv
"""
import csv
from collections import Counter
from pathlib import Path

SEEDS = Path(__file__).parents[2] / "jaffle_shop/seeds"
SEED = SEEDS / "raw_payments.csv"
CENTS = [500, 1200, 800, 1500, 600, 900, 1100, 400]

orders = list(csv.DictReader((SEEDS / "raw_orders.csv").open()))
payments = list(csv.DictReader(SEED.open()))
legs = Counter(p["order_id"] for p in payments)
single = [r["id"] for r in orders if r["ordered_at"].startswith("2018-02") and legs[r["id"]] == 1]
hit = single[::3][:len(CENTS)]
assert len(hit) == len(CENTS)
payment_id = max(int(r["id"]) for r in payments) + 1
new = [{"id": str(payment_id + i), "order_id": o, "payment_method": "bank_transfer", "amount": str(c)}
       for i, (o, c) in enumerate(zip(hit, CENTS))]

with SEED.open("a", newline="") as f:
    csv.DictWriter(f, fieldnames=payments[0].keys(), lineterminator="\n").writerows(new)

assert len(list(csv.DictReader(SEED.open()))) == len(payments) + len(new)
print(f"appended {len(new)} late second payments (ids {payment_id}-{payment_id + len(new) - 1}) for February orders "
      f"{hit}, {sum(CENTS) / 100:.2f} total")
