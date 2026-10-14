"""After a gateway release the second leg of every split-tender payment
arrives with an empty order reference.

The 13 orders paid with more than one payment keep their first payment and
lose the order_id on the last one. Nothing tests `not_null` on
`stg_payments.order_id`, so every dbt test passes, while the orphan payments
vanish from `orders`, `customers` and `region_revenue` (inner joins and a
group by on a NULL key) and only `stg_payments` still adds up to the
settlement.
Run from the repo root: python cases/join_drops_rows/inject.py
Undo: git checkout jaffle_shop/seeds/raw_payments.csv
"""
import csv
from collections import Counter
from pathlib import Path

SEED = Path(__file__).parents[2] / "jaffle_shop/seeds/raw_payments.csv"

rows = list(csv.DictReader(SEED.open()))
per_order = Counter(r["order_id"] for r in rows)
hit = []
for r in reversed(rows):  # the last payment of each multi-payment order
    if per_order[r["order_id"]] > 1 and r["order_id"] not in {h["order_id"] for h in hit}:
        hit.append(r)
for r in hit:
    r["order_id"] = ""

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"blanked order_id on {len(hit)} payments (ids {sorted(int(h['id']) for h in hit)}), "
      f"{sum(int(h['amount']) for h in hit) / 100:.2f} in total")
