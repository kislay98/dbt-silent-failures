# Ground truth: gift_card_dollars

**What broke:** `cases/gift_card_dollars/inject.py` rewrites the `amount` of all 12 `gift_card` rows in
`jaffle_shop/seeds/raw_payments.csv` from cents to dollars (2300 becomes 23, 600 becomes 6, and so on).
Ids, order ids and methods are untouched. It mimics a gift-card provider whose export switched units
while every other payment source kept sending cents.

**Where it shows:** `models/staging/stg_payments.sql` divides every `amount` by 100, assuming cents. The 12
gift card rows are divided a second time, so each is 100x too small (23.00 becomes 0.23). `models/orders.sql`
sums payments per order, so `gift_card_amount` and `amount` shrink for the 12 affected orders, and
`customers.customer_lifetime_value` shrinks for the 10 customers who own them. Row counts never change.

**Why nothing fails:** every dbt test still passes (28/28). The tests on payments are `unique`/`not_null` on
`payment_id` and `accepted_values` on `payment_method`; the tests on `orders` are `unique`/`not_null` on
`order_id`, `accepted_values` on `status`, and `not_null` on the amount columns. Nothing checks that an
amount is in a plausible range, that one method's totals move with the others, or that totals reconcile
to a provider statement.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(orders.amount)` | 1672.0 | 1469.05 |
| `sum(orders.gift_card_amount)` | 205.0 | 2.05 |
| `orders.amount` for order 9 (gift card only) | 23.0 | 0.23 |
| `orders.amount` for order 87 (gift card + another method) | 56.0 | 26.3 |
| `customers.customer_lifetime_value` for customer 53 | 39.0 | 16.23 |
| `customers.customer_lifetime_value` for customer 11 | 3.0 | 0.03 |
| `count(*) from stg_payments` | 113 | 113 |

Affected orders: 9, 19, 23, 43, 44, 58, 59, 62, 80, 87, 93, 96. Affected customers: 11, 22, 30, 31, 46, 53, 54, 57, 66, 90.

**Correct fix:** restore the seed (`git checkout jaffle_shop/seeds/raw_payments.csv`), or at the source fix the
feed to send cents again. A model fix that multiplies `gift_card` rows by 100 in `stg_payments` also makes
the output correct, but hard-codes the provider's mistake into the pipeline. A guard that would have caught it:
a singular test that fails when any `stg_payments.amount` is below 1.00 and not zero (every real payment here
is a whole dollar), or a `dbt_utils.accepted_range` on `stg_payments.amount` with `min_value: 1` for non-zero
rows, or a reconciliation of `sum(gift_card_amount)` to the provider statement (205.00).
