# Ground truth: units_and_rename

**What broke:** `cases/units_and_rename/inject.py` applies two feed changes to `jaffle_shop/seeds/raw_payments.csv` in
one load. From 1 March 2018 the payments feed writes a new `method` column (backfilled for all 113 rows) and leaves
`payment_method` empty on the 49 payments of orders placed from then on (as in `payment_method_renamed`). From
15 March the coupon provider reports amounts in dollars: payments 86, 93, 98, 106, 108 and 109 (orders 76, 81, 86,
92, 94 and 95) go from 200, 200, 2300, 200, 700, 2400 to 2, 2, 23, 2, 7, 24 (as in `coupon_cents`).

**Where it shows:** `stg_payments` reads `payment_method`, so it is NULL on 49 rows and the per-method columns in
`orders` are 0 from 1 March on: totals fall from 871.00, 185.00, 411.00, 205.00 to 513.00, 107.00, 210.00, 81.00.
It divides every amount by 100, so the six coupons become 0.02, 0.02, 0.23, 0.02, 0.07, 0.24 and `orders.amount`
is 1612.60 instead of 1672.00 (March 595.27 against 622.00, April 106.33 against 139.00). The breakdown cannot show
the shrunken coupons because their method is NULL too; the gap between `amount` and the sum of the method columns is
701.60. `customers` and `region_revenue` follow `amount`; `daily_revenue` is unchanged (the affected orders are older
than its three-day window).

**Why nothing fails:** every dbt test still passes (40/40): `accepted_values` ignores NULL, nothing tests `not_null` on
`payment_method`, the `not_null` tests on the method columns pass on 0, and nothing checks magnitudes.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(amount)` from `orders` | 1672.0 | 1612.6 |
| `sum(credit_card_amount)`, `sum(coupon_amount)`, `sum(bank_transfer_amount)`, `sum(gift_card_amount)` | 871.0, 185.0, 411.0, 205.0 | 513.0, 107.0, 210.0, 81.0 |
| `sum(amount) - sum(the four method columns)` from `orders` | 0.0 | 701.6 |
| March and April `sum(amount)` | 622.0, 139.0 | 595.27, 106.33 |
| `orders.coupon_amount`, `amount` for order 86 | 23.0, 23.0 | 0.0, 0.23 |
| `stg_payments` rows with NULL `payment_method` | 0 | 49 (701.6) |
| columns of `raw_payments` | id, order_id, payment_method, amount | plus `method` |

**Key number:** `select round(sum(amount) - sum(credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount), 2) from orders` is 0.0 clean and 701.6 broken (checked by `check_case.py`).

**Correct fix:** both changes, or the output stays wrong: read the new column in `stg_payments`
(`coalesce(payment_method, method)`), and multiply the six coupon payments from 15 March on by 100 at the source
(payment id 86 and later, or coupon rows under 100 cents). The rename alone leaves `amount` at 1612.60; the coupons
alone leave the method columns at 0 from March. Guards: `not_null` on `stg_payments.payment_method`, a test that the
four method columns add up to `amount`, and a sub-dollar payment check or a ledger reconciliation.
