# Ground truth: status_title_case

**What broke:** `cases/status_title_case/inject.py` rewrites `status` in `jaffle_shop/seeds/raw_orders.csv` for the 58
orders placed on or after 2018-02-15 (ids 42-99) into title case (`Completed`, `Placed`, `Shipped`, `Return_pending`),
and adds those spellings to the `accepted_values` lists for `status` in `jaffle_shop/models/schema.yml` and
`jaffle_shop/models/staging/schema.yml`. It mimics an app release that changed how it writes the enum, followed by an
on-call who widened the test to silence the alert. Two changes, one data and one config; the second makes the first
silent.

**Where it shows:** `models/customers.sql` computes `completed_lifetime_value` with
`case when orders.status = 'completed'`, a case-sensitive comparison, so the 31 completed orders from the cut-over on
(499.00) drop out of it for 28 customers. `orders.status` carries both spellings. `customer_lifetime_value`,
`orders.amount`, `daily_revenue` and `region_revenue` do not filter on status and are unchanged.

**Why nothing fails:** every dbt test still passes (40/40) because the widened `accepted_values` lists include the new
spellings. Nothing checks that a status has one spelling, that completed revenue reconciles to the ledger, or that a
test change came with a model change.

**How the output changes:**

| Measure | Clean | Broken |
|---|---|---|
| `sum(customers.completed_lifetime_value)` | 1103.0 | 604.0 |
| orders with `status` completed, Completed | 67, 0 | 36, 31 |
| orders with `status` placed, shipped, Placed, Shipped | 13, 13, 0, 0 | 0, 0, 13, 13 |
| distinct `status` values in `orders` | 5 | 7 |
| `customers.completed_lifetime_value` for customer 51 | 99.0 | 73.0 |
| `customers.completed_lifetime_value` for customer 13 | 26.0 | 0.0 |
| `accepted_values` for `status`, both schema files | 5 values | 10 values |
| `sum(orders.amount)`, `sum(customer_lifetime_value)` | 1672.0, 1672.0 | 1672.0, 1672.0 |

Affected customers: 3, 6, 8, 12, 13, 18, 19, 22, 25, 27, 30, 31, 34, 35, 36, 50, 51, 53, 54, 57, 63, 66, 69, 70, 71,
79, 92, 93. Total 499.00 = 1103.00 - 604.00.

**Correct fix:** normalise in staging (`lower(status) as status` in `stg_orders`) and put both `accepted_values` lists
back to the five lower-case values, or restore the seed and both schema files (`git checkout ...`). The guard existed:
the original `accepted_values` test fired, and widening it was the failure. What would have caught the widening is a
review rule that a test change needs a model change in the same PR, or a singular test that completed revenue
reconciles to the finance ledger.
