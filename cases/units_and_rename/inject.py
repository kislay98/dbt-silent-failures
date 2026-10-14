"""Two feed changes in one load. From 1 March 2018 the payments feed writes
`method` and leaves `payment_method` empty (`method` backfilled for every
row), and from 15 March the coupon provider reports amounts in dollars
instead of cents.

`stg_payments` still reads `payment_method`, so the per-method columns in
`orders` go to 0 from March; it still divides every amount by 100, so the
later coupons shrink a hundredfold and `orders.amount` is short. Every dbt
test passes: `accepted_values` ignores NULL and nothing checks magnitudes.
Run from the repo root: python cases/units_and_rename/inject.py
Rebuild with `dbt seed --full-refresh` then `dbt build`: the seed table gains a column.
Undo: git checkout jaffle_shop/seeds/raw_payments.csv
"""
import csv
from pathlib import Path

SEEDS = Path(__file__).parents[2] / "jaffle_shop/seeds"
SEED = SEEDS / "raw_payments.csv"
RENAMED_FROM, DOLLARS_FROM = "2018-03-01", "2018-03-15"

ordered_at = {r["id"]: r["ordered_at"] for r in csv.DictReader((SEEDS / "raw_orders.csv").open())}
rows = list(csv.DictReader(SEED.open()))
for r in rows:
    r["method"] = r["payment_method"]
    if ordered_at[r["order_id"]] >= RENAMED_FROM:
        r["payment_method"] = ""
renamed = [r for r in rows if r["payment_method"] == ""]
coupons = [r for r in rows if r["method"] == "coupon" and ordered_at[r["order_id"]] >= DOLLARS_FROM]
assert all(int(r["amount"]) % 100 == 0 for r in coupons), "expected whole-dollar cents"
for r in coupons:
    r["amount"] = str(int(r["amount"]) // 100)

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=[*rows[0].keys()], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"blanked payment_method for {len(renamed)} payments on orders from {RENAMED_FROM} (added method for all {len(rows)}); "
      f"rewrote {len(coupons)} coupon payments to dollars from {DOLLARS_FROM} (orders {[r['order_id'] for r in coupons]})")
