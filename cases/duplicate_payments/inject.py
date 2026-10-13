"""Re-ingest every 10th payment with a fresh id, like a retried batch load.

Rows are exact copies except `id`, so dbt's unique/not_null tests still pass,
but order amounts and customer lifetime value are double-counted downstream.
Run from the repo root: python cases/duplicate_payments/inject.py
Undo: git checkout jaffle_shop/seeds/raw_payments.csv
"""
import csv
from pathlib import Path

SEED = Path(__file__).parents[2] / "jaffle_shop/seeds/raw_payments.csv"

rows = list(csv.DictReader(SEED.open()))
next_id = max(int(r["id"]) for r in rows) + 1
dupes = [dict(r, id=str(next_id + i)) for i, r in enumerate(rows[::10])]

with SEED.open("a", newline="") as f:
    csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n").writerows(dupes)

assert len(list(csv.DictReader(SEED.open()))) == len(rows) + len(dupes)
print(f"appended {len(dupes)} duplicate payments for orders "
      f"{[r['order_id'] for r in dupes]}")
