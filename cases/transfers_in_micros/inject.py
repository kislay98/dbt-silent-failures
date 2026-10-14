"""From 15 March 2018 the bank transfer provider's v2 API reports amounts in
micro-units (one millionth of a dollar) instead of cents. Every bank transfer
on an order placed from that date on is rewritten from cents to micros; the
earlier transfers and every other method stay in cents.

Ids and methods are untouched, so dbt's unique/not_null/accepted_values tests
pass, while `stg_payments` divides by 100 as if the rows were cents and the
affected transfers come out ten thousand times too large in `orders`,
`customers` and the regional report.
Run from the repo root: python cases/transfers_in_micros/inject.py
Undo: git checkout jaffle_shop/seeds/raw_payments.csv
"""
import csv
from pathlib import Path

SEEDS = Path(__file__).parents[2] / "jaffle_shop/seeds"
SEED = SEEDS / "raw_payments.csv"
CUTOVER = "2018-03-15"
MICROS_PER_CENT = 10_000

ordered_at = {r["id"]: r["ordered_at"] for r in csv.DictReader((SEEDS / "raw_orders.csv").open())}
rows = list(csv.DictReader(SEED.open()))
hit = [r for r in rows if r["payment_method"] == "bank_transfer" and ordered_at[r["order_id"]] >= CUTOVER]
for r in hit:
    r["amount"] = str(int(r["amount"]) * MICROS_PER_CENT)

with SEED.open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=rows[0].keys(), lineterminator="\n")
    w.writeheader()
    w.writerows(rows)

assert len(list(csv.DictReader(SEED.open()))) == len(rows)
print(f"rewrote {len(hit)} of {sum(r['payment_method'] == 'bank_transfer' for r in rows)} bank transfers to micros "
      f"(payment ids {[r['id'] for r in hit]}, orders {[r['order_id'] for r in hit]}, placed from {CUTOVER})")
