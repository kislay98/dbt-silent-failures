# Ground truth: category_and_units

**What broke:** `cases/category_and_units/inject.py` makes two changes. In `jaffle_shop/seeds/raw_orders.csv` the 8
completed orders placed from 10 March 2018 (ids 66, 67, 68, 69, 70, 73, 75 and 76, 136.00) are written as
`delivered`, and `'delivered'` is added to the `accepted_values` lists in `jaffle_shop/models/schema.yml` and
`jaffle_shop/models/staging/schema.yml`, as in `status_new_value`. In `jaffle_shop/seeds/raw_payments.csv` the 6
coupon payments on orders placed from 15 March (payments 86, 93, 98, 106, 108 and 109 on orders 76, 81, 86, 92, 94 and
95) go from 200, 200, 2300, 200, 700, 2400 cents to 2, 2, 23, 2, 7, 24 dollars, as in `coupon_cents`. Order 76 is in
both sets: delivered, with its 2.00 coupon now 0.02.

**Where it shows:** `customers.completed_lifetime_value` filters on `status = 'completed'`, so the eight delivered
orders drop out: 967.00 instead of 1103.00 (customer 3 from 65.00 to 46.00, customer 36 from 28.00 to 0). In `orders`
March has 9 completed orders worth 143.00 and 8 delivered worth 134.02, not 136.00, because `stg_payments` divides
the six dollar coupons by 100 again: `coupon_amount` sums to 125.60 instead of 185.00, `orders.amount` to 1612.60,
March to 595.27 and April to 106.33, and `region_revenue` follows (NSW 312.02, QLD 282.56, TAS 128.02).
`daily_revenue` is unchanged. Counting `delivered` as completed brings completed revenue to 1101.02 in total and
277.02 in March, 1.98 short of the admin's 1103.00 and 279.00: the coupon on order 76.

**Why nothing fails:** every dbt test still passes (44/44): the widened `accepted_values` lists accept `delivered`,
`not_null` passes on 0.02, and nothing reconciles completed revenue to the admin or coupon revenue to the provider.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(completed_lifetime_value)` from `customers` | 1103.0 | 967.0 |
| March `orders` by status: completed, delivered | 17 (279.0), 0 | 9 (143.0), 8 (134.02) |
| `sum(amount)` from `orders` where status in (completed, delivered) | 1103.0 | 1101.02 |
| the same for March | 279.0 | 277.02 |
| `orders.status`, `coupon_amount`, `amount` for order 76 | completed, 2.0, 2.0 | delivered, 0.02, 0.02 |
| `coupon_amount` for orders 81, 86, 92, 94, 95 | 2.0, 23.0, 2.0, 7.0, 24.0 | 0.02, 0.23, 0.02, 0.07, 0.24 |
| `sum(coupon_amount)`, `sum(amount)` from `orders` | 185.0, 1672.0 | 125.6, 1612.6 |
| March, April `sum(amount)` from `orders` | 622.0, 139.0 | 595.27, 106.33 |
| `completed_lifetime_value` for customers 3, 8, 19, 36, 53, 79 | 65, 45, 3, 28, 39, 27 | 46, 19, 0, 0, 23, 4 |
| `accepted_values` list for status in both schema files | 5 values | 6 values |

**Key number:** `select round(sum(amount), 2) from orders where status in ('completed', 'delivered')` is 1103.0 clean and 1101.02 broken (checked by `check_case.py`).

**Correct fix:** both changes, or the output stays wrong: `delivered` counts as completed (mapped to `completed` in
`stg_orders`, or the filter in `customers` widened, with the `accepted_values` lists matching what the models read),
and the six coupon rows go back to cents (times 100 for coupons on orders placed from 15 March, or the provider's
export restated; a rule on coupons under 1.00 also works, the clean data has none). The status change alone leaves
completed revenue at 1101.02; the coupon fix alone leaves it at 967.00. Guards: a reconciliation of completed revenue
to the admin, a test that no coupon payment is under 1.00, and a rule that a widened `accepted_values` list comes with
the model change that reads the new value.
