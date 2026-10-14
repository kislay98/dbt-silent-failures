# Ground truth: fanout_and_nulls

**What broke:** `cases/fanout_and_nulls/inject.py` applies two feed changes in one load. The processor feed lists
credit cards under two processors, so `jaffle_shop/seeds/raw_payment_methods.csv` gains a second `credit_card` row
(`adyen`, same 0.029 rate; 5 rows, no new column). The payments feed leaves `amount` empty on the second instalment
of eight split orders in `jaffle_shop/seeds/raw_payments.csv`: payments 10, 15, 21, 30, 55, 58, 62 and 67 (orders 9,
13, 18, 25, 49, 51, 54 and 58), 0, 1400, 800, 1600, 900, 100, 1100 and 600 cents, 65.00 in all (payment 10 was 0 and
changes no total).

**Where it shows:** `orders` joins the lookup before it sums the payments, so every credit card payment that still has
an amount is counted twice; `sum()` skips the NULL amounts, so the eight orders lose their second instalment. The two
pull in opposite directions: `credit_card_amount` is 1706.00 instead of 871.00 (twice the 853.00 of non-NULL card
payments), the other three columns are 169.00, 386.00 and 199.00 instead of 185.00, 411.00 and 205.00, `orders.amount`
is 2460.00 instead of 1672.00 and `fees` 51.46 instead of 27.41. Order 2 shows 40.00 for 20.00, order 13 10.00 for
19.00, order 25 64.00 for 58.00 (card doubled, coupon gone). `customers` and `region_revenue` read the payments without
the lookup, so they only lose the NULLs: 1607.00. `daily_revenue` rebuilds 9 April at 48.00 (1696.00 in total).

**Why nothing fails:** every dbt test still passes (44/44): nothing tests the lookup key for uniqueness or `not_null`
on the payment amount, and the order totals are not NULL because the other instalment is there.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(amount)`, `sum(fees)` from `orders` | 1672.0, 27.41 | 2460.0, 51.46 |
| `sum` of credit_card, coupon, bank_transfer, gift_card columns | 871.0, 185.0, 411.0, 205.0 | 1706.0, 169.0, 386.0, 199.0 |
| `sum(amount)` by month in `orders` | 496.0, 415.0, 622.0, 139.0 | 668.0, 679.0, 950.0, 163.0 |
| `orders.amount` for orders 2, 13, 18, 25, 54, 58 | 20.0, 19.0, 13.0, 58.0, 29.0, 24.0 | 40.0, 10.0, 10.0, 64.0, 36.0, 18.0 |
| `stg_payments` rows with NULL `amount` | 0 | 8 (65.0) |
| `sum(customer_lifetime_value)`, `sum(revenue)` from `region_revenue` | 1672.0, 1672.0 | 1607.0, 1607.0 |
| `sum(revenue)` from `daily_revenue` | 1672.0 | 1696.0 (9 April at 48.0) |
| rows in `raw_payment_methods` | 4 | 5, two for `credit_card` |

**Key number:** `select round(sum(amount), 2) from orders` is 1672.0 clean and 2460.0 broken (checked by `check_case.py`).

**Correct fix:** both changes, or the output stays wrong: one row per payment method in the lookup (remove the second
`credit_card` row or collapse the lookup before the join) and the eight amounts restored from the processor at the
source (ids 10, 15, 21, 30, 55, 58, 62, 67). The lookup alone leaves `orders.amount` at 1607.00 and the eight orders
short; the amounts alone leave credit cards doubled at 2543.00. Then `dbt build --full-refresh`. Guards: a `unique`
test on `raw_payment_methods.payment_method`, `not_null` on `stg_payments.amount`, and a singular test that
`sum(orders.amount)` equals `sum(stg_payments.amount)`.
