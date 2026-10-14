"""Same failure as utc_switch, harder prompt: from 2018-03-01 the orders feed
sends `ordered_at` in UTC instead of the shop's local time (UTC+05:30),
without telling anyone. The prompt gives no example order, only the app's
daily counts for March and the shop's country.

Run from the repo root: python cases/utc_switch_blind/inject.py
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
