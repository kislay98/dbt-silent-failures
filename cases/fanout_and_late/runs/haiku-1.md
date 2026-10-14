claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 33 s | USD 0.006 | 2026-10-14 | project 8ee5734 | prompt 89287ed414a2

## 1. Root cause

**File:** `jaffle_shop/models/daily_revenue.sql`

`daily_revenue` is incremental with `delete+insert` on `order_date`. On incremental runs, its `is_incremental()` filter only rebuilds `order_date >= max(order_date) - 3 days`, where the max is taken from the existing `daily_revenue` table. The table already holds April 2018 rows, so the window starts in early April. The backfilled orders are dated in February, so they fall outside the window and no run ever rebuilds those dates. `orders` is a full table rebuild, so it picked them up. `daily_revenue` kept its stale February rows.

The numbers confirm this:
- 33 − 27 = 6 orders and 480.00 − 415.00 = 65.00, which matches the six backfilled orders worth 65.00.
- Every mismatched row in the sample is a February date.

The comment in the model ("payments can land a day or two after the order") describes a different problem. The window is keyed on `order_date`, not load time, so any late-arriving order with an old date is missed no matter how long the lookback is. Widening it to 7 days would not reliably fix this.

The tests passed because the `daily_revenue` tests only check `not_null` and `unique` on its own columns. Nothing compares it to `orders`.

**Separate bug, same gap:** `region_revenue` totals 1838.0 in revenue, but `orders` totals 1737.0. `region_revenue` sums payments after joining to `orders` and `raw_customer_regions`. That seed has an `updated_at` column, which suggests more than one row per customer. Each extra region row duplicates that customer's payments, which would explain the 101 of over-count. I can't see the seed rows, so verify with:

```sql
select customer_id, count(*) from raw_customer_regions group by 1 having count(*) > 1
```

The schema only tests `not_null` on `customer_id`.

I haven't run anything. These findings come from the files and the output sample.

## 2. Fix

**Immediate data fix:**

```
dbt build --select daily_revenue --full-refresh
```

A full refresh rebuilds the table from `orders`, which corrects February. Then rerun the reconciliation query from the output sample and confirm it returns no rows.

**Durable code fix:** `daily_revenue` is a cheap group-by over `orders`, and `orders` is already rebuilt in full each run. The incremental logic adds the failure mode above and saves almost nothing. Make it a table:

```sql
{{
    config(
        materialized='table'
    )
}}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders
    group by order_date

)

select * from final
```

If the table ever grows enough to need incremental builds, the window has to be driven by which `order_date`s changed, not by `max(order_date)`. That requires an `updated_at` on the orders source, which the raw table doesn't have.

**Region fix:** dedupe `raw_customer_regions` to one row per customer, keeping the latest `updated_at`, for example in a staging model that `region_revenue` refs. Then add `unique` to `customer_id` in the seed's schema entry.

## 3. Test that would have caught this

Add a singular test at `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`. It compares every `order_date` across the full history and fails on any difference in count or revenue. `dbt build` runs it after the model, so the nightly build fails when the backfill arrives.

```sql
with expected as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}
    group by order_date

),

actual as (

    select * from {{ ref('daily_revenue') }}

)

select
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue

from expected
full outer join actual
    on expected.order_date = actual.order_date

where coalesce(expected.orders, 0) <> coalesce(actual.orders, 0)
   or abs(coalesce(expected.revenue, 0) - coalesce(actual.revenue, 0)) > 0.005
```

Against the current data, this test should fail on the February dates. It is the check that would have failed before the dashboard disagreed.

For the region bug, the `unique` test on `raw_customer_regions.customer_id` would have failed on the ingestion load.
