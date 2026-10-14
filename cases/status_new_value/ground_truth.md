# Ground truth: status_new_value

**What broke:** `cases/status_new_value/inject.py` rewrites the status of the 8 completed orders placed from 10 March
2018 (ids 66, 67, 68, 69, 70, 73, 75 and 76, 136.00) in `jaffle_shop/seeds/raw_orders.csv` from `completed` to
`delivered`, and adds `'delivered'` to the `accepted_values` lists in `jaffle_shop/models/schema.yml` and
`jaffle_shop/models/staging/schema.yml`. It mimics a new fulfilment flow that introduced a status, and an on-call who
widened the test to stop the alert.

**Where it shows:** `models/customers.sql` sums `completed_lifetime_value` over `status = 'completed'`, so the eight
orders drop out of it: 967.00 instead of 1103.00 in total, and six customers lose value (customer 3 goes from 65.00 to
46.00, customer 36 from 28.00 to 0.00). In `orders`, March has 9 completed orders worth 143.00 and 8 delivered worth
136.00 instead of 17 completed worth 279.00. `customer_lifetime_value`, `orders.amount`, `region_revenue` and
`daily_revenue` are unchanged.

**Why nothing fails:** every dbt test still passes (40/40) because the only test that would have fired,
`accepted_values` on status, was widened to include the new value, and nothing reconciles completed revenue to the
admin figure. `docs.md` still lists five statuses.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(completed_lifetime_value)` from `customers` | 1103.0 | 967.0 |
| `sum(customer_lifetime_value)` from `customers` | 1672.0 | 1672.0 |
| March `orders` by status: completed, delivered | 17 (279.0), 0 | 9 (143.0), 8 (136.0) |
| `completed_lifetime_value` for customers 3, 8, 19, 36, 53, 79 | 65, 45, 3, 28, 39, 27 | 46, 19, 0, 0, 23, 4 |
| `orders.status` for orders 66 to 70, 73, 75, 76 | completed | delivered |
| `accepted_values` list for status in both schema files | 5 values | 6 values |

**Key number:** `select round(sum(completed_lifetime_value), 2) from customers` is 1103.0 clean and 967.0 broken (checked by `check_case.py`).

**Correct fix:** count delivered orders as completed, either `status in ('completed', 'delivered')` in
`models/customers.sql` or a mapping of `delivered` to `completed` in `stg_orders`, and update `docs.md`; or restore
the seed and the two schema files (`git checkout ...`). Reverting the `accepted_values` lists alone makes the build
fail, which is a guard, not a fix. Guards: a singular test that reconciles completed revenue to the admin figure, and
a review rule that a widened `accepted_values` list needs a model change with it.
