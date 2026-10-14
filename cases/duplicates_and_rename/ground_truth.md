# Ground truth: duplicates_and_rename

**What broke:** `cases/duplicates_and_rename/inject.py` does two things to `jaffle_shop/seeds/raw_payments.csv`. It
adds a `method` column, backfilled for all 113 rows, and blanks `payment_method` on the 49 payments (ids 65 to 113) of
orders placed from 1 March 2018, as in `payment_method_renamed`; and it appends copies of payments 68, 78, 88, 98 and
108 (orders 59, 68, 77, 86 and 94: a gift card of 28.00, a credit card of 16.00, a bank transfer of 19.00 and coupons of
23.00 and 7.00, 93.00 in all) as ids 114 to 118, in the new format, as a retried first batch would.

**Where it shows:** `stg_payments` still reads `payment_method`, so it is NULL on 54 rows and the per-method columns in
`orders` are 0 for the 42 orders from 1 March on (57 to 99): the totals drop from 871.00, 185.00, 411.00 and 205.00
to 513.00, 107.00, 210.00 and 81.00, and March and April show 0.00 for every method. The copies double `orders.amount`
for their five orders (59: 56.00, 68: 32.00, 77: 38.00, 86: 46.00, 94: 14.00), so `sum(orders.amount)` is 1765.00
instead of 1672.00, March 708.00 instead of 622.00, April 146.00 instead of 139.00; `customers` (customers 30, 35, 53,
63 and 68; completed revenue 1147.00 instead of 1103.00) and `region_revenue` (1765.00) follow. `daily_revenue` keeps
1672.00, because none of the five orders falls in its three-day window. Copies and originals sit side by side in
`stg_payments` with the same order, amount and NULL method (118 rows instead of 113).

**Why nothing fails:** every dbt test still passes (44/44): `accepted_values` on `payment_method` ignores NULL, nothing
tests `not_null` on it, the `not_null` tests on the per-method columns pass on 0, and the copies have fresh ids.
Nothing checks one payment per order, method and amount, or reconciles the total to the settlement.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(credit_card_amount)`, `sum(coupon_amount)`, `sum(bank_transfer_amount)`, `sum(gift_card_amount)` from `orders` | 871.0, 185.0, 411.0, 205.0 | 513.0, 107.0, 210.0, 81.0 |
| March, April `sum(amount)` from `orders` | 622.0, 139.0 | 708.0, 146.0 |
| March `sum(credit_card_amount)` from `orders` | 334.0 | 0.0 |
| `sum(amount)` from `orders` | 1672.0 | 1765.0 |
| `orders.amount` for orders 59, 68, 77, 86, 94 | 28.0, 16.0, 19.0, 23.0, 7.0 | 56.0, 32.0, 38.0, 46.0, 14.0 |
| `sum(completed_lifetime_value)` from `customers` | 1103.0 | 1147.0 |
| `count(*)` from `stg_payments`, rows with NULL `payment_method` | 113, 0 | 118, 54 |
| `sum(revenue)` from `daily_revenue` | 1672.0 | 1672.0 |
| columns of `raw_payments` | id, order_id, payment_method, amount | plus `method` |

**Key number:** `select round(sum(amount), 2) from orders` is 1672.0 clean and 1765.0 broken (checked by `check_case.py`).

**Correct fix:** both changes, or the output stays wrong: `stg_payments` reads `coalesce(payment_method, method)`
(or `method`, which is backfilled for every row), and the five copies go (ids 114 to 118, or one row per order,
method and amount, which the clean data allows: no order has two payments of the same method and amount). The
rename alone leaves `sum(orders.amount)` at 1765.00 with the copies now counted in their method columns too; dropping
the copies alone leaves every method column at 0 from March. Guards: `not_null` on `stg_payments.payment_method`, a
uniqueness test on order, method and amount, and a reconciliation of `sum(orders.amount)` to the settlement.
