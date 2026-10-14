"""From 15 March 2018 the coupon provider's export reports amounts in dollars
instead of cents, like a provider that changed its API without a notice.
Every coupon payment on an order placed from that date on is rewritten from
cents to dollars; the earlier coupon rows and every other method stay in cents.

Ids and methods are untouched, so dbt's unique/not_null/accepted_values tests
pass, while `stg_payments` divides those rows by 100 again and coupon revenue
in `orders`, `customers` and the daily and regional reports shrinks 100x from
the switch on.
Run from the repo root: python cases/coupon_cents/inject.py
Undo: git checkout jaffle_shop/seeds/raw_payments.csv
"""
import csv
from pathlib import Path

SEEDS = Path(__file__).parents[2] / "jaffle_shop/seeds"
SEED = SEEDS / "raw_payments.csv"
CUTOVER = "2018-03-15"

ordered_at = {r["id"]: r["ordered_at"] for r in csv.DictReader((SEEDS / "raw_orders.csv").open())}
rows = list(csv.DictReader(SEED.open()))
hit = [r for r in rows if r["payment_method"] == "coupon" and ordered_at[r["order_id"]] >= CUTOVER]
assert all(int(r["amount"]) % 100 == 0 for r in hit), "expected whole-dollar cents"
for r in hit:
    r["amount"] = str(int(r["amount"]) // 100)

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"rewrote {len(hit)} of {sum(r['payment_method'] == 'coupon' for r in rows)} coupon payments to dollars "
      f"(payment ids {[r['id'] for r in hit]}, orders {[r['order_id'] for r in hit]}, placed from {CUTOVER})")
