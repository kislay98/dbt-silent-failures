"""Two feed changes in one load. The processor feed lists credit cards under
two processors (stripe and adyen, same fee rate), so `raw_payment_methods`
has two `credit_card` rows; and the payments feed leaves `amount` empty on
the second instalment of eight split orders, like an export that blanks a
transfer that has not settled yet.

`orders` joins the lookup before it sums the payments, so every credit card
payment with an amount is counted twice; `sum()` skips the NULL amounts, so
the eight orders lose their second instalment. The two pull `orders.amount`
in opposite directions. Every dbt test passes: nothing tests the lookup key
for uniqueness or `not_null` on the payment amount, and the order totals are
not NULL because the other instalment is there.
Run from the repo root: python cases/fanout_and_nulls/inject.py
Undo: git checkout jaffle_shop/seeds/raw_payment_methods.csv jaffle_shop/seeds/raw_payments.csv
"""
import csv
from collections import defaultdict
from pathlib import Path

SEEDS = Path(__file__).parents[2] / "jaffle_shop/seeds"
LOOKUP, PAYMENTS = SEEDS / "raw_payment_methods.csv", SEEDS / "raw_payments.csv"

methods = list(csv.DictReader(LOOKUP.open()))
stripe = next(r for r in methods if r["payment_method"] == "credit_card")
with LOOKUP.open("a", newline="") as f:
    csv.DictWriter(f, fieldnames=methods[0].keys(), lineterminator="\n").writerows([dict(stripe, processor="adyen")])
assert len(list(csv.DictReader(LOOKUP.open()))) == len(methods) + 1

rows = list(csv.DictReader(PAYMENTS.open()))
legs = defaultdict(list)
for r in rows:
    legs[r["order_id"]].append(r)
multi = sorted((o for o in legs if len(legs[o]) > 1), key=int)[:8]
hit = [max(legs[o], key=lambda r: int(r["id"])) for o in multi]
blanked = sum(int(r["amount"]) for r in hit)
for r in hit:
    r["amount"] = ""
with PAYMENTS.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
assert len(list(csv.DictReader(PAYMENTS.open()))) == len(rows)

print(f"added a second credit_card row (adyen) to raw_payment_methods; blanked amount on {len(hit)} second instalments "
      f"(payment ids {[r['id'] for r in hit]}, orders {multi}), {blanked / 100:.2f} total")
