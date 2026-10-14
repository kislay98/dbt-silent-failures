"""Rename the payments feed's method column going forward: for payments on
orders placed from 2018-03-01 the feed writes `method` and leaves
`payment_method` empty. `method` is backfilled for every row, as a migrated
feed would do.

`stg_payments` still reads `payment_method`, so it is NULL for every payment
from the cut-over on. `accepted_values` ignores NULL and nothing tests
`not_null` on it, so every dbt test passes, while the per-method columns in
`orders` (`credit_card_amount` and the rest) silently go to 0 for those
orders. `orders.amount` and every total stay right.
Run from the repo root: python cases/payment_method_renamed/inject.py
Rebuild with `dbt seed --full-refresh` then `dbt build`: the seed table gains a column.
Undo: git checkout jaffle_shop/seeds/raw_payments.csv
"""
import csv
from pathlib import Path

SEEDS = Path(__file__).parents[2] / "jaffle_shop/seeds"
SEED = SEEDS / "raw_payments.csv"
CUTOVER = "2018-03-01"

ordered_at = {r["id"]: r["ordered_at"] for r in csv.DictReader((SEEDS / "raw_orders.csv").open())}
rows = list(csv.DictReader(SEED.open()))
for r in rows:
    r["method"] = r["payment_method"]
    if ordered_at[r["order_id"]] >= CUTOVER:
        r["payment_method"] = ""
hit = [r for r in rows if r["payment_method"] == ""]

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=[*rows[0].keys()], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"blanked payment_method for {len(hit)} payments on orders placed from {CUTOVER} on "
      f"(payment ids {hit[0]['id']}-{hit[-1]['id']}), added method for all {len(rows)}")
