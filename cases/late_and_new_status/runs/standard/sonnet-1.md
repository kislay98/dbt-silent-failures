claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 35 s | USD 0.105 | 2026-10-14 | project ad0b16b | prompt 2b2290cfef14

## Summary

There are two independent bugs. Both pass the tests because the tests don't check what the numbers mean.

## 1. Root causes

**A. "Completed" ignores `delivered` (`models/customers.sql`, `customer_payments` CTE).**
`completed_amount` uses `orders.status = 'completed'`, but `raw_orders` now carries a `delivered` status as well. It is not in the standard jaffle statuses, and both `accepted_values` lists were widened to allow it, so no test fires. The numbers reconcile exactly:

| Source | Amount |
|---|---|
| `status = 'completed'` in `orders` | 967.00 |
| `status = 'delivered'` in `orders` | 201.00 |
| Sum | 1168.00 (the admin figure) |

March follows the same pattern: 143.00 completed plus 136.00 delivered gives the admin's 279.00. I can't see the March `delivered` rows in the output, so the 136.00 is inferred from the gap. The admin treats `delivered` as completed. `orders.status` is passed through untouched, so anyone filtering `status = 'completed'` on it has the same blind spot.

**B. Frozen history in `models/daily_revenue.sql`.**
The model is incremental with `delete+insert`. On each run it only rebuilds `order_date >= max(order_date) - 3 days`. The max is April 7, so everything before about April 4 is never recomputed. February in `orders` is 33 orders / 480.00, but `daily_revenue` has 27 / 415.00. The missing 6 orders / 65.00 were not in `orders` when the dates were first built. The cause is late or backfilled ingestion, or payments landing after the order. They were never picked up because they sit outside the lookback window. January, March and April match only because nothing late arrived there. The `unique` and `not_null` tests can't see missing rows.

## 2. Fix

**A.** Define "completed" once and use it everywhere. Confirm with finance that `delivered` counts as completed first.

```sql
-- customers.sql
sum(case when orders.status in ('completed', 'delivered') then amount else 0 end) as completed_amount
```

Better still, add an `is_completed` column in `stg_orders.sql` and reference that in `customers.sql` and anywhere else that needs it:

```sql
status in ('completed', 'delivered') as is_completed
```

**B.** Repair the data now:

```
dbt run --full-refresh -s daily_revenue
```

To stop it recurring, make `daily_revenue` a plain `table`. It has 69 rows, so there is no reason for it to be incremental. If it must stay incremental, key the lookback on load time rather than business date, or add a periodic full refresh.

## 3. Tests

**Reconciliation between `daily_revenue` and `orders`** (`tests/assert_daily_revenue_matches_orders.sql`). It catches bug B and any other stale dates:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
),

d as (
    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}
)

select coalesce(o.order_date, d.order_date) as order_date
from o
full outer join d on o.order_date = d.order_date
where coalesce(o.orders, 0) != coalesce(d.orders, 0)
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

**Status coverage** (for bug A). Widening `accepted_values` should not be a silent change. Add a singular test that fails whenever a status has no explicit business meaning, such as a seed or var mapping each status to completed or not. At minimum, reconcile `customers` against `orders` using the shared definition:

```sql
select 1
from (select sum(completed_lifetime_value) as v from {{ ref('customers') }}) c
cross join (
    select sum(amount) as v from {{ ref('orders') }} where is_completed
) o
where abs(c.v - o.v) > 0.005
```

The most direct guard is an `expression_is_true` or singular test that compares the completed total to the admin or source-system figure, for example from a finance seed. That is the check that would have caught the 1168 vs 967 gap.
