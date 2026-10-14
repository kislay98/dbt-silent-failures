claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 23 s | USD 0.062 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

The bug is in `models/daily_revenue.sql`. It is an incremental model (`delete+insert`) that only rebuilds `order_date >= max(order_date) - 3 days`, and the max date is in April. Any February order that reaches `orders` after its February day was first materialized falls outside that window and is never picked up. The table is already built, so each run only rewrites the last three days of April.

The evidence, from your output:
- `orders` and `daily_revenue` agree for January, March and April, and differ only on six February dates (02-03, 02-08, 02-13, 02-17, 02-22, 02-26).
- There are 105 orders versus 99 counted in `daily_revenue`, so 6 orders are missing. Those 6 orders account for the 65.00 gap (480 − 415, and 1737 − 1672).
- On 02-03 and 02-22 the whole date row is absent. On the other four dates the count is short by one.
- Nothing fails because every `daily_revenue` test (`unique`, `not_null`) checks only that table's internal consistency. Nothing reconciles it with `orders`.

The model's own comment shows the assumption: it expects late data only within a day or two of the max date. Here the late rows are about two months old. I can't see the seed data, so I can't say whether they are late-arriving orders or backfilled/corrected ones. Either way, the incremental table never revisits them.

## 2. Fix

Immediate repair, which rebuilds the table from `orders`:

```
dbt run --full-refresh -s daily_revenue
```

Durable fix: this is a ~100-row aggregate over a table that is already rebuilt in full on every run, so incremental buys nothing. Make it a plain table:

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

If it must stay incremental, the lookback can't be anchored to `max(order_date)`. It would need to key off a load or updated timestamp, which `raw_orders` and `raw_payments` don't currently have.

## 3. Test that would have caught it

Add `tests/assert_daily_revenue_matches_orders.sql`. It returns a row for every date where the two models disagree:

```sql
with o as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

),

d as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily_revenue

from o
full outer join d on o.order_date = d.order_date

where o.order_date is null
   or d.order_date is null
   or o.orders <> d.orders
   or abs(o.revenue - d.revenue) > 0.005
```

With the current incremental table, this test returns the six February rows and fails the build.

One unrelated thing I noticed: `raw_customer_regions` is described as "one row per customer", but the only uniqueness test is on `region` in `region_revenue`. Adding `unique` on `raw_customer_regions.customer_id` would guard against a duplicate customer inflating regional revenue through the join. I haven't verified that this is happening.
