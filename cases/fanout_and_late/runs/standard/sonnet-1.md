claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 19 s | USD 0.084 | 2026-10-14 | project ad0b16b | prompt 6c2f9fd42779

## 1. Root cause

**`models/daily_revenue.sql`**: the incremental filter is keyed on the business date, not on when the data arrived:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

On each run, only dates within 3 days of the latest `order_date` already in the table (early April 2018) get deleted and reinserted. Every earlier date is frozen at whatever it held when first built. The comment assumes late data is at most a day or two late, relative to the newest order.

The February backfill broke that assumption. It added six completed February orders and their payments to `raw_orders` and `raw_payments`, 65.00 in total. `orders` is a full table rebuild, so it picked them up: 33 orders and 480.00. `daily_revenue` never touched those February dates, so it still shows 27 and 415.00. The gaps are 33−27 = 6 orders and 480−415 = 65.00, which is exactly the backfill.

All 35 steps passed because every test looks at one table in isolation:
- `unique` and `not_null` on `daily_revenue` hold for a stale table.
- Nothing compares `daily_revenue` to `orders`.

The other models (`customers`, `region_revenue`) are full rebuilds and aren't affected. I haven't run anything. This is from the files and output you pasted.

## 2. Fix

Rebuild the table now:

```
dbt run --full-refresh -s daily_revenue
```

To stop it recurring, change `daily_revenue.sql`. The raw tables have no load timestamp, so there's no reliable way to tell which dates changed. A fixed lookback will always miss something. At 105 orders, a full rebuild is also cheap. I'd make it a plain table:

```sql
{{ config(materialized='table') }}

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

If it needs to stay incremental at larger scale, add a load timestamp (`_loaded_at`) in ingestion. Then filter on the dates touched by recently loaded rows rather than on `max(order_date)`. Update the `schema.yml` description too, since it still says "Rebuilds the last three days".

## 3. Test

A singular test, `tests/assert_daily_revenue_matches_orders.sql`, reconciles the two models per date. It returns rows on any mismatch, including a missing or extra date:

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
    coalesce(expected.order_date, daily_revenue.order_date) as order_date,
    expected.orders as expected_orders,
    daily_revenue.orders as actual_orders,
    expected.revenue as expected_revenue,
    daily_revenue.revenue as actual_revenue

from expected
full outer join {{ ref('daily_revenue') }} as daily_revenue
    on expected.order_date = daily_revenue.order_date

where expected.order_date is null
   or daily_revenue.order_date is null
   or expected.orders != daily_revenue.orders
   or abs(coalesce(expected.revenue, 0) - coalesce(daily_revenue.revenue, 0)) > 0.005
```

Against last night's build, this would have failed on every date the backfill touched. That would have given you the failure at 18:21 instead of a dashboard complaint from ops.
