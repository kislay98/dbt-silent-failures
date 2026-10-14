claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 17 s | USD 0.082 | 2026-10-14 | project ad0b16b | prompt 6c2f9fd42779

## 1. Root cause

`models/daily_revenue.sql` is the cause. Its incremental filter only rebuilds the last three days relative to the latest date already in the table:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

That window is keyed on `order_date`, not on when the data arrived. The comment assumes late data lands a day or two after the order. The backfill delivered February orders in April, so the filter never saw them. Only the early-April days were recomputed, and the February rows kept their pre-backfill values.

The numbers fit this:
- `orders` was rebuilt as a full table, so it has the 6 backfilled orders: 33 orders and 480.00 for February.
- `daily_revenue` has 27 orders and 415.00 for February. The gap is exactly 6 orders and 65.00.
- Every test passes because none of them compares the two models. `unique`/`not_null` on `daily_revenue` hold for stale rows too.

The data is fine; the model is wrong.

## 2. Fix

Immediate repair: `dbt run --full-refresh -s daily_revenue`.

Durable fix: with about 100 orders there is no reason for this model to be incremental. Make it a plain table so it always matches `orders`:

```sql
{{ config(materialized='table') }}

with orders as (

    select * from {{ ref('orders') }}

)

select
    order_date,
    count(*) as orders,
    sum(amount) as revenue

from orders
group by order_date
```

If it needs to stay incremental at scale, the window has to key on a load timestamp from the source (for example `_loaded_at`). The raw tables don't have one today, and an `order_date` lookback can never handle backfills.

## 3. Test that would have caught it

`tests/assert_daily_revenue_matches_orders.sql`, a singular test that returns any date where the two models disagree:

```sql
with expected as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(e.order_date, d.order_date) as order_date,
    e.orders as expected_orders,
    d.orders as actual_orders,
    e.revenue as expected_revenue,
    d.revenue as actual_revenue
from expected e
full outer join {{ ref('daily_revenue') }} d
    on e.order_date = d.order_date
where e.order_date is null
   or d.order_date is null
   or e.orders != d.orders
   or abs(e.revenue - d.revenue) > 0.005
```

This returns the affected February dates today.

## Separate issue, not the cause of this mismatch

`region_revenue` is also overstated. Its regions total is 1,838.00 against 1,737.00 in `orders`. `raw_customer_regions` has 106 rows for 100 customers, because the `updated_at` column shows it is a history feed with several rows per customer. The join in `region_revenue.sql` therefore fans out. Take the latest row per customer, and add a `unique` test on `raw_customer_regions.customer_id`, which the current schema lacks.

I haven't run anything against the project; this comes from reading the files and outputs. I can apply the changes if you'd like.
