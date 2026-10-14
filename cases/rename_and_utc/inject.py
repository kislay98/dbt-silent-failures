"""A new export job for the orders feed, live from 1 March 2018. It writes
`order_ts` instead of `ordered_at` and writes it in UTC; the backfill of
`order_ts` for the older rows copied `ordered_at` as it was, in the shop's
local time (UTC+05:30). From 1 March `ordered_at` is empty.

`stg_orders` still reads `ordered_at`, so `order_date` is NULL for every
order from 1 March: March and April vanish from the monthly report and
`daily_revenue` keeps its old rows. Reading `order_ts` alone brings the
orders back a day early where they were placed before 05:30 local time,
because the rows from the cut-over are in UTC. Every dbt test passes:
nothing tests `not_null` on the dates or what a timestamp means.
Run from the repo root: python cases/rename_and_utc/inject.py
Rebuild with `dbt seed --full-refresh` then `dbt build`: the seed table gains a column.
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
    r["order_ts"] = r["ordered_at"]
    if r["ordered_at"] >= CUTOVER:
        local = datetime.fromisoformat(r["ordered_at"])
        utc = local - SHIFT
        if utc.date() != local.date():
            moved.append(r["id"])
        r["order_ts"] = utc.strftime("%Y-%m-%d %H:%M:%S")
        r["ordered_at"] = ""
        hit.append(r)

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=[*rows[0].keys()], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"added order_ts for all {len(rows)} orders; from {CUTOVER} blanked ordered_at and wrote order_ts in UTC for "
      f"{len(hit)} orders (ids {hit[0]['id']}-{hit[-1]['id']}), {len(moved)} of them change date: ids {moved}")
