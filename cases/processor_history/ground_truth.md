# Ground truth: processor_history

**What broke:** `cases/processor_history/inject.py` rewrites `jaffle_shop/seeds/raw_payment_methods.csv` the way a
feed that started carrying history would: a new `effective_from` column, backfilled with 2018-01-01 on the four
existing rows, and a second `credit_card` row for the move from Stripe to Adyen on 1 March 2018 at the same fee rate
(0.029). The Stripe row stays. The lookup goes from 4 rows and 3 columns to 5 rows and 4 columns.

**Where it shows:** `orders` joins the lookup on `payment_method` alone before it sums the payments, so each of the
55 credit card payments is counted twice, in every month and not only from March: `credit_card_amount` is 1742.00
instead of 871.00 (436.00, 590.00, 668.00, 48.00 by month against 218.00, 295.00, 334.00, 24.00), `orders.amount`
2543.00 instead of 1672.00 and `fees` 52.63 instead of 27.41. Order 1 shows 20.00 for a 10.00 charge, order 13 shows
24.00 instead of 19.00. `daily_revenue` picks up the doubling for 9 April only, inside its three-day window (48.00
instead of 24.00, 1696.00 in total). `stg_payments`, `customers` and `region_revenue` read the payments without the
lookup and stay right.

**Why nothing fails:** every dbt test still passes (44/44): nothing tests the lookup key for uniqueness, `orders` is
still one row per order, and `not_null` passes on inflated values.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(credit_card_amount)`, `sum(amount)`, `sum(fees)` from `orders` | 871.0, 1672.0, 27.41 | 1742.0, 2543.0, 52.63 |
| credit cards by month in `orders` | 218.0, 295.0, 334.0, 24.0 | 436.0, 590.0, 668.0, 48.0 |
| `orders.credit_card_amount` for orders 1, 2, 6, 13 | 10.0, 20.0, 6.0, 5.0 | 20.0, 40.0, 12.0, 10.0 |
| `orders.amount` for orders 1, 2, 6, 13 | 10.0, 20.0, 6.0, 19.0 | 20.0, 40.0, 12.0, 24.0 |
| `sum(revenue)` from `daily_revenue` | 1672.0 | 1696.0 (9 April rebuilt at 48.0) |
| `sum(amount)` from `stg_payments` by method, `sum(revenue)` from `region_revenue` | 411.0, 185.0, 871.0, 205.0; 1672.0 | unchanged |
| `raw_payment_methods` | 4 rows, columns payment_method, processor, fee_rate | 5 rows, plus `effective_from`; two for `credit_card` |

**Key number:** `select round(sum(credit_card_amount), 2) from orders` is 871.0 clean and 1742.0 broken (checked by `check_case.py`).

**Correct fix:** one lookup row per payment per join: pick the row in effect on the order date (join the order date
into `order_payments` and take the latest `effective_from` at or before it), or the latest row per `payment_method`;
both give the clean output here because the rate did not change. Removing the Adyen row at the source
(`git checkout jaffle_shop/seeds/raw_payment_methods.csv`) also restores the output but throws away the history the
feed now carries. Then `dbt build --full-refresh` so `daily_revenue` rebuilds 9 April. Guards: a singular test that
`sum(orders.amount)` equals `sum(stg_payments.amount)`, or a reconciliation of the credit card column to the processor
statement; a `unique` test on (`payment_method`, `effective_from`) passes on history rows and does not catch this, a
`unique` test on `payment_method` alone fires on every history row.
