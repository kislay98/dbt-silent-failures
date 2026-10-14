# Ground truth: coupon_cents

**What broke:** `cases/coupon_cents/inject.py` rewrites 6 of the 13 coupon rows in `jaffle_shop/seeds/raw_payments.csv`
from cents to dollars: payments 86, 93, 98, 106, 108 and 109 (orders 76, 81, 86, 92, 94 and 95, placed from 15 March
2018) go from 200, 200, 2300, 200, 700, 2400 to 2, 2, 23, 2, 7, 24. The 7 earlier coupon rows and every other method
stay in cents. It mimics a coupon provider whose export switched units mid-stream.

**Where it shows:** `stg_payments` divides every amount by 100, so those six payments become 0.02, 0.02, 0.23, 0.02,
0.07 and 0.24. `orders.coupon_amount` sums to 125.60 instead of 185.00 and `orders.amount` to 1612.60 instead of
1672.00; March coupon revenue is 18.27 instead of 45.00 and April 0.33 instead of 33.00, while January and February
are unchanged. `customers` and `region_revenue` follow (NSW 314.00 to 312.02, QLD 338.00 to 282.56, TAS 130.00 to
128.02). `daily_revenue` is unchanged, because the incremental run rebuilds 6 to 9 April only and the affected
orders are older.

**Why nothing fails:** every dbt test still passes (40/40): ids and methods are untouched, `not_null` on the amount
columns passes on small values, and nothing checks the magnitude of a payment or reconciles coupon revenue to the
provider.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(coupon_amount)`, `sum(amount)` from `orders` | 185.0, 1672.0 | 125.6, 1612.6 |
| `orders.coupon_amount` for orders 76, 81, 86, 92, 94, 95 | 2, 2, 23, 2, 7, 24 | 0.02, 0.02, 0.23, 0.02, 0.07, 0.24 |
| March, April `sum(coupon_amount)` | 45.0, 33.0 | 18.27, 0.33 |
| `region_revenue.revenue` for NSW, QLD, TAS | 314.0, 338.0, 130.0 | 312.02, 282.56, 128.02 |
| `raw_payments.amount` for payments 86, 93, 98, 106, 108, 109 | 200, 200, 2300, 200, 700, 2400 | 2, 2, 23, 2, 7, 24 |
| `sum(revenue)` from `daily_revenue` | 1672.0 | 1672.0 |

**Key number:** `select round(sum(coupon_amount), 2) from orders` is 185.0 clean and 125.6 broken (checked by `check_case.py`).

**Correct fix:** restore the six rows (`git checkout jaffle_shop/seeds/raw_payments.csv`), or multiply the coupon
payments from the switch on (payment id 86 and later, orders placed from 15 March) by 100 at the source. A blanket
"times 100 on every coupon row" is wrong: it pushes the seven earlier coupons to 100 times their value. Guards: a
test that raw amounts are whole cents of at least 100 (no coupon under 1.00 in the history), or a singular test
that reconciles coupon revenue by month to the provider's ledger.
