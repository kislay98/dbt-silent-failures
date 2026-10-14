# Ground truth: payment_method_renamed

**What broke:** `cases/payment_method_renamed/inject.py` adds a `method` column to `jaffle_shop/seeds/raw_payments.csv`,
backfilled for all 113 rows, and blanks `payment_method` on the 49 payments (ids 65 to 113) of orders placed from
1 March 2018. It mimics a payments feed that renamed the column going forward and migrated the history.

**Where it shows:** `stg_payments` still reads `payment_method`, so it is NULL on 49 rows worth 761.00. In
`models/orders.sql` the per-method columns are `sum(case when payment_method = '...' then amount else 0 end)`, so
`credit_card_amount`, `coupon_amount`, `bank_transfer_amount` and `gift_card_amount` are 0 for the 42 orders from 1
March on that have an amount (orders 57 to 99), while `orders.amount` is right. The totals drop from 871.00, 185.00,
411.00 and 205.00 to 513.00, 107.00, 210.00 and 81.00; March and April show 0.00 for every method against totals of
622.00 and 139.00. `customers`, `region_revenue` and `daily_revenue` read `amount` only and are unchanged.

**Why nothing fails:** every dbt test still passes (40/40): `accepted_values` on `payment_method` ignores NULL,
nothing tests `not_null` on it, and the `not_null` tests on the per-method columns pass on 0. Loading the seed in full
(`dbt seed --full-refresh`) adds the column without complaint.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(credit_card_amount)`, `sum(coupon_amount)`, `sum(bank_transfer_amount)`, `sum(gift_card_amount)` from `orders` | 871.0, 185.0, 411.0, 205.0 | 513.0, 107.0, 210.0, 81.0 |
| March `sum(credit_card_amount)`, `sum(amount)` from `orders` | 334.0, 622.0 | 0.0, 622.0 |
| orders with `amount > 0` and every method column 0 | 0 | 42 (orders 57 to 99) |
| `stg_payments` rows with NULL `payment_method` | 0 | 49 (761.0) |
| `sum(amount)` from `orders` | 1672.0 | 1672.0 |
| columns of `raw_payments` | id, order_id, payment_method, amount | plus `method` |

**Key number:** `select round(sum(credit_card_amount), 2) from orders` is 871.0 clean and 513.0 broken (checked by `check_case.py`).

**Correct fix:** read the new column in `stg_payments` (`coalesce(payment_method, method) as payment_method` while
both exist, or `method as payment_method` since it is backfilled), or restore the feed
(`git checkout jaffle_shop/seeds/raw_payments.csv`). Guards: `not_null` on `stg_payments.payment_method`, or a singular
test that the four method columns add up to `amount` for every order.
