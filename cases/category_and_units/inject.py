"""Two things change in March. From 10 March the new fulfilment flow writes
`delivered` where the app used to write `completed`; the `accepted_values`
tests on status failed on the first run and the on-call added the value to
both schema files to stop the alert, as in `status_new_value`. From 15 March
the coupon provider's export reports amounts in dollars instead of cents, as
in `coupon_cents`.

`customers.completed_lifetime_value` filters on `status = 'completed'`, so
every delivered order drops out of it; `stg_payments` divides the dollar
coupons by 100 again, so one delivered order and five open ones lose almost
all of their coupon amount. Counting `delivered` as completed then still
leaves completed revenue 1.98 short of the admin. Every dbt test passes.
Run from the repo root: python cases/category_and_units/inject.py
Undo: git checkout jaffle_shop/seeds/raw_orders.csv jaffle_shop/seeds/raw_payments.csv jaffle_shop/models/schema.yml jaffle_shop/models/staging/schema.yml
"""
import csv
from pathlib import Path

SHOP = Path(__file__).parents[2] / "jaffle_shop"
SEEDS = SHOP / "seeds"
SCHEMAS = [SHOP / "models/schema.yml", SHOP / "models/staging/schema.yml"]
STATUS_CUTOVER = "2018-03-10"
COUPON_CUTOVER = "2018-03-15"
OLD = "values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']"
NEW = OLD[:-1] + ", 'delivered']"

orders = list(csv.DictReader((SEEDS / "raw_orders.csv").open()))
payments = list(csv.DictReader((SEEDS / "raw_payments.csv").open()))
ordered_at = {r["id"]: r["ordered_at"] for r in orders}
delivered = [r for r in orders if r["ordered_at"] >= STATUS_CUTOVER and r["status"] == "completed"]
for r in delivered:
    r["status"] = "delivered"
coupons = [r for r in payments if r["payment_method"] == "coupon" and ordered_at[r["order_id"]] >= COUPON_CUTOVER]
assert all(int(r["amount"]) % 100 == 0 for r in coupons), "expected whole-dollar cents"
for r in coupons:
    r["amount"] = str(int(r["amount"]) // 100)

for name, rows in [("raw_orders.csv", orders), ("raw_payments.csv", payments)]:
    with (SEEDS / name).open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    assert len(list(csv.DictReader((SEEDS / name).open()))) == len(rows)
for p in SCHEMAS:
    s = p.read_text()
    assert s.count(OLD) == 1, p
    p.write_text(s.replace(OLD, NEW))

print(f"wrote delivered for {len(delivered)} completed orders from {STATUS_CUTOVER} on (ids {delivered[0]['id']}-{delivered[-1]['id']}); "
      f"widened accepted_values in {len(SCHEMAS)} schema files; rewrote {len(coupons)} coupon payments to dollars "
      f"(payment ids {[r['id'] for r in coupons]}, orders {[r['order_id'] for r in coupons]}, placed from {COUPON_CUTOVER})")
