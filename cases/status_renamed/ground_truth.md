# Ground truth: status_renamed

**What broke:** `cases/status_renamed/inject.py` rewrites `jaffle_shop/seeds/raw_orders.csv` with a fifth column,
`order_status`, filled for every row, and empties `status` for the 43 orders placed on or after 2018-03-01 (ids 57-99).
It mimics an orders feed that renamed the column at a release, backfilled history into the new name, and kept the old
column, now empty, for compatibility. The seed table gains a column, so rebuild with `dbt seed --full-refresh` then
`dbt build` (what `check_case.py` does).

**Where it shows:** `models/staging/stg_orders.sql` still reads `status`, so `stg_orders.status` and `orders.status`
are NULL for those 43 orders. `models/customers.sql` computes `completed_lifetime_value` with
`case when orders.status = 'completed'`, which is false for NULL, so the 17 completed orders in that window (279.00)
drop out of it for 16 customers. `customer_lifetime_value`, `orders.amount`, `daily_revenue` and `region_revenue` do
not filter on status and are unchanged.

**Why nothing fails:** every dbt test still passes (40/40). `accepted_values` on `status` in `stg_orders` and `orders`
ignores NULL, `status` has no `not_null` test, and the seed has no column contract, so the extra column loads without
complaint. Nothing checks that status is populated or that completed revenue reconciles to anything.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(customers.completed_lifetime_value)` | 1103.0 | 824.0 |
| orders with `status` NULL | 0 | 43 (2018-03-01 to 2018-04-09) |
| orders with `status = 'completed'`, their amount | 67, 1103.0 | 50, 824.0 |
| orders with `status` placed, shipped | 13, 13 | 0, 0 |
| `customers.completed_lifetime_value` for customer 13 | 26.0 | 0.0 |
| `customers.completed_lifetime_value` for customer 3 | 65.0 | 46.0 |
| columns of `raw_orders` | id, user_id, ordered_at, status | id, user_id, ordered_at, status, order_status |
| `sum(orders.amount)`, `sum(customer_lifetime_value)` | 1672.0, 1672.0 | 1672.0, 1672.0 |

Affected customers (clean to broken): 3 (65 to 46), 8 (45 to 19), 12 (4 to 0), 13 (26 to 0), 19 (3 to 0), 22 (29 to 5),
25 (24 to 22), 30 (28 to 0), 36 (28 to 0), 53 (39 to 23), 57 (26 to 12), 63 (16 to 0), 69 (32 to 13), 70 (29 to 0),
79 (27 to 4), 93 (2 to 0). Total 279.00 = 1103.00 - 824.00.

**Correct fix:** read the new column in `stg_orders` (`coalesce(order_status, status) as status`, or `order_status`
alone since it is backfilled), or restore the seed (`git checkout jaffle_shop/seeds/raw_orders.csv`). Guards that
would have caught it: a `not_null` test on `stg_orders.status`, or a singular test that fails when any order from the
last load has no status.
