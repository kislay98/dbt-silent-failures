"""The processor feed starts listing bank transfers under both settlement
banks, so `raw_payment_methods` carries two rows for `bank_transfer` (anz
and westpac, same fee rate): a lookup whose grain changed without anyone
telling the warehouse.

`orders` joins the lookup before it sums the payments, so every bank
transfer is counted twice: `bank_transfer_amount`, `amount` and `fees`
inflate in `orders` and `customers` follows. Every dbt test passes: nothing
tests the lookup key for uniqueness and `orders` is still one row per order.
Run from the repo root: python cases/payment_methods_fanout/inject.py
Undo: git checkout jaffle_shop/seeds/raw_payment_methods.csv
"""
import csv
from pathlib import Path

SEED = Path(__file__).parents[2] / "jaffle_shop/seeds/raw_payment_methods.csv"

rows = list(csv.DictReader(SEED.open()))
anz = next(r for r in rows if r["payment_method"] == "bank_transfer")
new = dict(anz, processor="westpac")

with SEED.open("a", newline="") as f:
    csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n").writerows([new])

assert len(list(csv.DictReader(SEED.open()))) == len(rows) + 1
print(f"added a second bank_transfer row (processor westpac, fee_rate {new['fee_rate']}) to raw_payment_methods")
