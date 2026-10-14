# Ground truth: null_payment_amounts

**What broke:** `cases/null_payment_amounts/inject.py` blanks `amount` in `jaffle_shop/seeds/raw_payments.csv` on the
second instalment of the first eight split orders: payments 10, 15, 21, 30, 55, 58, 62 and 67 (orders 9, 13, 18, 25,
49, 51, 54 and 58), 65.00 in total, one of which (payment 10) was already 0. It mimics a processor export that blanks
the amount of an instalment that has not settled.

**Where it shows:** `stg_payments` carries the NULL and every `sum` skips it, so `orders.amount` for seven orders drops
to the first instalment (order 25 from 58.00 to 42.00, order 13 from 19.00 to 5.00), `sum(orders.amount)` goes from
1672.00 to 1607.00, and the method columns, `customers` and `region_revenue` follow (January 496.00 to 458.00,
February 415.00 to 394.00, March 622.00 to 616.00). `daily_revenue` is unchanged at 1672.00: the incremental run never
revisits those days, so the dashboard still agrees with the settlement.

**Why nothing fails:** every dbt test still passes (40/40): nothing tests `not_null` on the payment amount, and the
`not_null` tests on `orders.amount` and the method columns pass because the other instalment is there and `sum` of a
row set with one NULL is not NULL.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(amount)` from `orders` | 1672.0 | 1607.0 |
| `sum(credit_card_amount)`, `sum(coupon_amount)`, `sum(bank_transfer_amount)`, `sum(gift_card_amount)` | 871.0, 185.0, 411.0, 205.0 | 853.0, 169.0, 386.0, 199.0 |
| `orders.amount` for orders 13, 25, 54 | 19.0, 58.0, 29.0 | 5.0, 42.0, 18.0 |
| `stg_payments` rows with NULL `amount` | 0 | 8 |
| `sum(customer_lifetime_value)` from `customers` | 1672.0 | 1607.0 |
| `sum(revenue)` from `daily_revenue` | 1672.0 | 1672.0 |

**Key number:** `select round(sum(amount), 2) from orders` is 1672.0 clean and 1607.0 broken (checked by `check_case.py`).

**Correct fix:** the amounts are gone from the feed, so reload them from the processor
(`git checkout jaffle_shop/seeds/raw_payments.csv`); `coalesce(amount, 0)` or dropping the rows hides the loss. Guards:
`not_null` on `stg_payments.amount`, a reconciliation of `sum(orders.amount)` to the settlement, or of `orders` to
`daily_revenue` by day.
