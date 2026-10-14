claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 30 s | USD 0.084 | 2026-10-14 | project 8ee5734 | prompt 0a1f2799e0c1

## 1. Root cause

**`models/daily_revenue.sql`** is the problem, and the data is fine. `orders` is correct. The model is incremental (`delete+insert`) and only rebuilds `order_date >= max(order_date) - 3 days`. Its `max(order_date)` is 2018-04-09, so only 6 April onward is ever recomputed.

Last night's backfill added orders 100-105, which are dated 31 Jan to 25 Feb. They sit months before that window, so `daily_revenue` never picked them up. The rows were never rebuilt and nothing errored, so every test passed. The existing tests only check shape (`unique`, `not_null`), and none compares `daily_revenue` with `orders`.

Your diff query confirms it. Each mismatched day is short by exactly the backfilled order:
- 22 Feb is missing entirely (order 104).
- The other days are short by one order each.
- The gaps total 6 orders and 65.00 (6+12+9+15+20+3), the same six orders and amount ops expects.

**Date shift in the Jan/Feb split.** The backfill is not exactly what ops sees, because `order_date` is UTC and the shop screen is local. The offsets fit +05:30:
- 100 is 2018-01-31 23:18:19 UTC, which is 2018-02-01 04:48:19 local, matching ops' first order.
- 101 is 2 Feb 20:42 UTC, which is 3 Feb local.
- 105 is 25 Feb 19:29 UTC, which is 26 Feb local.

So by UTC date, five of the six land in February and order 100 lands in January. That is why January's 29 vs 30 also differs. Even after the fix, February is 32 orders and 474.00 in both tables, not 33 and 539.00. That is a timezone boundary, not a bug. `orders.order_date` is documented as UTC, so ops reconciling by it against the shop's local view will always see edge-of-day orders shift.

## 2. Fix

Repair now: `dbt run --full-refresh -s daily_revenue`. This brings both tables back in line.

Durable fix: `daily_revenue` is a cheap aggregate over a table that is already fully rebuilt each run, so incremental buys nothing. Make it a table:

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

Also update the `schema.yml` description, which currently says "Incremental, rebuilds the last three days".

If you keep it incremental, the lookback needs to be driven by arrival time, not order date. That means the ingestion job adds a `loaded_at` column and the model reprocesses the dates touched by newly loaded rows.

For ops, add a local-date column to `stg_orders` and `orders` and reconcile on that, keeping `order_date` as UTC. I inferred +05:30 from the data, so confirm the shop's real timezone first:

```sql
cast((ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata' as date) as order_date_local
```

## 3. Test that would have caught it

Save this as `tests/assert_daily_revenue_matches_orders.sql`. It returns rows only where the two models disagree, so any row fails the test:

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
    o.revenue as orders_revenue,
    d.revenue as daily_revenue

from o
full outer join d
    on o.order_date = d.order_date

where coalesce(o.orders, 0) <> coalesce(d.orders, 0)
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Against the current state it would have returned the six rows from your diff query and failed the build.

I haven't run any of this. The analysis is from the files, log and query output you pasted.
