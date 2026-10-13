"""From 2018-03-01 the orders feed sends `ordered_at` in UTC instead of the
shop's local time (UTC+05:30), without telling anyone.

Every order from the cut-over on is 5h30 earlier, so orders placed between
00:00 and 05:29 local time land on the previous `order_date`. Nothing tests
what a timestamp means, so every dbt test passes, while daily counts, the
monthly split and customers' first and last order dates move.
Run from the repo root: python cases/utc_switch/inject.py
Undo: git checkout jaffle_shop/seeds/raw_orders.csv
"""
import csv
from datetime import datetime, timedelta
from pathlib import Path

SEED = Path(__file__).parents[2] / "jaffle_shop/seeds/raw_orders.csv"
CUTOVER = "2018-03-01"
SHIFT = timedelta(hours=5, minutes=30)

rows = list(csv.DictReader(SEED.open()))
hit, moved = [], []
for r in rows:
    if r["ordered_at"] >= CUTOVER:
        utc = datetime.fromisoformat(r["ordered_at"]) - SHIFT
        if utc.date() != datetime.fromisoformat(r["ordered_at"]).date():
            moved.append(r["id"])
        r["ordered_at"] = utc.strftime("%Y-%m-%d %H:%M:%S")
        hit.append(r)

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"shifted {len(hit)} orders from {CUTOVER} on by -5:30; {len(moved)} changed date: ids {moved}")
