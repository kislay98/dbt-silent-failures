"""The processor feed starts carrying history: a new `effective_from` column,
backfilled with 2018-01-01 on the existing rows, and a second `credit_card`
row for the move from Stripe to Adyen on 1 March 2018 at the same fee rate.
The Stripe row stays, as a feed that keeps history would keep it.

`orders` joins the lookup on `payment_method` alone before it sums the
payments, so every credit card payment is counted twice in every month:
`credit_card_amount`, `amount` and `fees` inflate and `customers` follows.
Every dbt test passes: nothing tests the lookup key for uniqueness.
Run from the repo root: python cases/processor_history/inject.py
Rebuild with `dbt seed --full-refresh` then `dbt build`: the seed table gains a column.
Undo: git checkout jaffle_shop/seeds/raw_payment_methods.csv
"""
import csv
from pathlib import Path

SEED = Path(__file__).parents[2] / "jaffle_shop/seeds/raw_payment_methods.csv"
CUTOVER = "2018-03-01"

rows = list(csv.DictReader(SEED.open()))
for r in rows:
    r["effective_from"] = "2018-01-01"
stripe = next(r for r in rows if r["payment_method"] == "credit_card")
rows.append(dict(stripe, processor="adyen", effective_from=CUTOVER))

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=[*rows[0].keys()], lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"added effective_from to raw_payment_methods and a second credit_card row (adyen from {CUTOVER}, "
      f"fee_rate {stripe['fee_rate']}); {len(rows)} rows")
