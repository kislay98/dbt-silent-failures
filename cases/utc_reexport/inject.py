"""The orders feed is re-exported in UTC for its whole history: every
`ordered_at` moves back 5 hours 30 minutes, like a source that switched its
export to UTC and backfilled the old rows the same way.

`stg_orders` takes `cast(ordered_at as date)`, so every order placed before
05:30 local lands on the previous day, across all months. Every dbt test
passes. The dates are wrong everywhere; the fix is the same conversion on
every row, with no cut-over to find.
Run from the repo root: python cases/utc_reexport/inject.py
Undo: git checkout jaffle_shop/seeds/raw_orders.csv
"""
import csv
from datetime import datetime, timedelta
from pathlib import Path

SEED = Path(__file__).parents[2] / "jaffle_shop/seeds/raw_orders.csv"
OFFSET = timedelta(hours=5, minutes=30)

rows = list(csv.DictReader(SEED.open()))
moved = 0
for r in rows:
    local = datetime.strptime(r["ordered_at"], "%Y-%m-%d %H:%M:%S")
    utc = local - OFFSET
    moved += utc.date() != local.date()
    r["ordered_at"] = utc.strftime("%Y-%m-%d %H:%M:%S")

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"shifted all {len(rows)} orders by -5:30; {moved} land on the previous day")
