"""Two feed changes in one load. The gift card provider's export switched to
dollars, so every gift card payment is a hundred times too small once
`stg_payments` divides by 100 again; and a retried batch re-ingested every
10th payment with a fresh id, so twelve payments are counted twice.

The two pull `orders.amount` in opposite directions and nearly cancel in
the total. Every dbt test passes: the replayed rows have new ids, and
nothing checks magnitudes.
Run from the repo root: python cases/duplicates_and_units/inject.py
Undo: git checkout jaffle_shop/seeds/raw_payments.csv
"""
import csv
from pathlib import Path

SEED = Path(__file__).parents[2] / "jaffle_shop/seeds/raw_payments.csv"

rows = list(csv.DictReader(SEED.open()))
gift = [r for r in rows if r["payment_method"] == "gift_card"]
assert all(int(r["amount"]) % 100 == 0 for r in gift), "expected whole-dollar cents"
for r in gift:
    r["amount"] = str(int(r["amount"]) // 100)
next_id = max(int(r["id"]) for r in rows) + 1
dupes = [dict(r, id=str(next_id + i)) for i, r in enumerate(rows[::10])]

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
    w.writeheader()
    w.writerows(rows + dupes)

assert len(list(csv.DictReader(SEED.open()))) == len(rows) + len(dupes)
print(f"rewrote {len(gift)} gift_card payments to dollars (orders {[r['order_id'] for r in gift]}); "
      f"appended {len(dupes)} duplicate payments (ids {next_id}-{next_id + len(dupes) - 1}) for orders {[r['order_id'] for r in dupes]}")
