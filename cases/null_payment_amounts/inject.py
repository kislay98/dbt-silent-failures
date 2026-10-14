"""The payments feed leaves `amount` empty on the second instalment of eight
split orders, like a processor export that blanks the amount of a transfer
that has not settled yet.

`stg_payments` carries the NULL through, `sum()` skips it, so `orders.amount`
and the per-method columns for those orders silently drop to the first
instalment, and `customers` and `region_revenue` follow. Every dbt test
passes: nothing tests `not_null` on the payment amount, and the order totals
are not NULL because the other instalment is there.
Run from the repo root: python cases/null_payment_amounts/inject.py
Undo: git checkout jaffle_shop/seeds/raw_payments.csv
"""
import csv
from collections import defaultdict
from pathlib import Path

SEED = Path(__file__).parents[2] / "jaffle_shop/seeds/raw_payments.csv"

rows = list(csv.DictReader(SEED.open()))
legs = defaultdict(list)
for r in rows:
    legs[r["order_id"]].append(r)
multi = sorted((o for o in legs if len(legs[o]) > 1), key=int)[:8]
hit = [max(legs[o], key=lambda r: int(r["id"])) for o in multi]
blanked = sum(int(r["amount"]) for r in hit)
for r in hit:
    r["amount"] = ""

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"blanked amount on {len(hit)} second instalments (payment ids {[r['id'] for r in hit]}, orders {multi}), "
      f"{blanked / 100:.2f} total")
