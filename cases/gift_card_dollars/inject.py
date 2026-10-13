"""Rewrite every gift_card payment amount from cents to dollars, like a provider
whose export switched units without telling anyone.

Rows keep their ids and methods, so dbt's unique/not_null/accepted_values tests
still pass, but `stg_payments` divides by 100 again and gift card revenue
shrinks 100x in `orders` and `customers`.
Run from the repo root: python cases/gift_card_dollars/inject.py
Undo: git checkout jaffle_shop/seeds/raw_payments.csv
"""
import csv
from pathlib import Path

SEED = Path(__file__).parents[2] / "jaffle_shop/seeds/raw_payments.csv"

rows = list(csv.DictReader(SEED.open()))
hit = [r for r in rows if r["payment_method"] == "gift_card"]
assert all(int(r["amount"]) % 100 == 0 for r in hit), "expected whole-dollar cents"
for r in hit:
    r["amount"] = str(int(r["amount"]) // 100)

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"rewrote {len(hit)} gift_card payments to dollars for orders "
      f"{[r['order_id'] for r in hit]}")
