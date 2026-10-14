"""Two things land with the payments feed's migration. For payments on orders
placed from 2018-03-01 the feed writes `method` and leaves `payment_method`
empty, with `method` backfilled for every row, as in `payment_method_renamed`;
and the first batch in the new format was retried after a timeout, so every
10th payment of the migrated rows was loaded twice, the copies with fresh ids.

`stg_payments` still reads `payment_method`, so the per-method columns in
`orders` go to 0 from the cut-over on, and the five copied payments double
`orders.amount` for their orders. `accepted_values` ignores NULL, ids are
fresh, so every dbt test passes.
Run from the repo root: python cases/duplicates_and_rename/inject.py
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
next_id = max(int(r["id"]) for r in rows) + 1
dupes = [dict(r, id=str(next_id + i)) for i, r in enumerate(hit[3::10])]

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=[*rows[0].keys()], lineterminator="\n")
    w.writeheader()
    w.writerows(rows + dupes)

assert len(list(csv.DictReader(SEED.open()))) == len(rows) + len(dupes)
print(f"blanked payment_method for {len(hit)} payments on orders placed from {CUTOVER} on "
      f"(payment ids {hit[0]['id']}-{hit[-1]['id']}), added method for all {len(rows)}; appended {len(dupes)} copies "
      f"(ids {next_id}-{next_id + len(dupes) - 1}) of payments {[r['id'] for r in hit[3::10]]} for orders "
      f"{[r['order_id'] for r in dupes]}, {sum(int(r['amount']) for r in dupes) / 100:.2f}")
