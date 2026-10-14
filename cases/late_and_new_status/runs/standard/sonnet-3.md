claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 26 s | USD 0.094 | 2026-10-14 | project ad0b16b | prompt 2b2290cfef14

## 1. Root cause

There are two independent problems. Both pass the tests because the tests only check shape (unique, not_null, accepted_values), never totals.

**A. `delivered` is not counted as completed (`models/customers.sql`, `models/staging/schema.yml`, `models/schema.yml`)**

- `customers.sql` computes `completed_amount` with `orders.status = 'completed'` only.
- The data has a second terminal status, `delivered`: 14 orders worth 201.00.
- The numbers reconcile exactly:
  - 967.00 (completed) + 201.00 (delivered) = 1168.00, which matches the admin.
  - In March, 143.00 + 136.00 = 279.00, so about 136.00 of the delivered amount is in March.
- The sample shows order 75 as `delivered` on 2018-03-18, which fits the idea that the source started emitting `delivered` for finished orders around March.
- `delivered` was added to both `accepted_values` lists, so the new value was accepted silently and never mapped to "completed".
- I couldn't see the seed data, so the March split is inferred from the arithmetic. Confirm it with `select date_trunc('month', order_date), sum(amount) from orders where status='delivered' group by 1`, which should show 136.00 for March.

**B. `daily_revenue` is stale for February (`models/daily_revenue.sql`)**

- The model is incremental with `delete+insert`, but it only rebuilds `order_date >= max(order_date) - 3 days`.
- `orders` has 33 February orders worth 480.00. `daily_revenue` has 27 worth 415.00, so 6 orders and 65.00 are missing.
- Total revenue matches the admin, so `orders` is right and the daily table is behind it. February orders (or their payments) were loaded after the fact, well outside the 3-day window, so they were never picked up.
- That also explains "always matched": the table was fine until the late data landed.
- The comment says payments land a day or two late, but nothing handles older backfills. The existing tests can't see this, since `daily_revenue` is internally consistent.

## 2. Fix

**A.** Define completion once in `orders` and reuse it:

```sql
-- orders.sql, final CTE
orders.status in ('completed', 'delivered') as is_completed,
```

```sql
-- customers.sql, customer_payments CTE (also select orders.is_completed in the orders CTE from ref('orders'))
sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

Alternatively, use `status in ('completed', 'delivered')` directly in `customers.sql`. Finance should confirm that `delivered` counts as completed. Expected result: `completed_lifetime_value` sums to 1168.00.

**B.** Rebuild now with `dbt run --full-refresh -s daily_revenue`. To prevent a repeat, either:

- make it `materialized='table'`, since it is about 70 rows and the incremental logic buys nothing, which is my recommendation; or
- keep it incremental and widen or replace the lookback, for example reprocess any date whose `orders` aggregate differs from the stored one, and run a periodic full refresh.

Expected result: February becomes 33 orders and 480.00.

## 3. Tests

Singular test `tests/assert_daily_revenue_matches_orders.sql`, which returns rows on any mismatch:

```sql
select o.order_date, o.orders, d.orders as daily_orders, o.revenue, d.revenue as daily_revenue
from (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
) o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or o.revenue is distinct from d.revenue
```

Singular test `tests/assert_completed_value_matches_orders.sql`, which catches a status that isn't mapped:

```sql
select 1
from (
    select
        (select sum(amount) from {{ ref('orders') }} where status in ('completed', 'delivered')) as o,
        (select sum(completed_lifetime_value) from {{ ref('customers') }}) as c
)
where abs(o - c) > 0.005
```

For the new-status problem, consider changing the status `accepted_values` test to `severity: warn`, or keeping a status-to-category mapping seed. That way a new status forces a decision about how to classify it, instead of being accepted without anyone noticing.
