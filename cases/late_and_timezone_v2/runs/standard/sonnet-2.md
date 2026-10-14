claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 37 s | USD 0.099 | 2026-10-15 | project ad0b16b | prompt 5e3ee671b766

## 1. Root cause

The bug is in `models/daily_revenue.sql`. `orders` is correct, and `daily_revenue` is stale.

The incremental filter is `where order_date >= (select max(order_date) - interval 3 day from {{ this }})`. It only rebuilds the last three days before the newest date already in the table. The late-arriving store's backfill is dated January to February, while the table's max date is in April. So `delete+insert` never touches those dates, and the backfilled orders never reach `daily_revenue`.

The numbers confirm this:
- The backfill is 6 orders and 65.00 by the shop screen's dates. Order 100 is dated 31 Jan in `orders`, and its payment 114 is 600 cents, so 6.00.
- The other 5 orders are in February: 474 − 415 = 59.00 and 32 − 27 = 5 orders.
- January shows the same gap, 30 vs 29 orders and 502 vs 496 revenue. That is order 100, 6.00, so the missing total is 59 + 6 = 65.00.
- `unique`/`not_null` tests on `daily_revenue` can't see this, because the table is internally consistent and just out of date. Every test passes.

**Order 100 and time zones.** The offset between `raw_orders.ordered_at` (23:18:19 on 31 Jan) and the shop screen (04:48:19 on 1 Feb) is +5:30. So `ordered_at` is UTC and the shop admin is in local time. `stg_orders` does `cast(ordered_at as date)`, which gives UTC dates, and that matches the documented "Date (UTC)" on `orders.order_date`. This isn't what causes the `orders` vs `daily_revenue` mismatch. But when ops compares by `order_date` against the shop screen, any order placed after 18:30 UTC will land on a different day, and order 100 is one of them. That already happens for existing orders such as order 28 (22:50 UTC on 29 Jan). Whether ops wants local dates is a business decision. If so, add a separate `order_date_local` rather than silently changing the UTC grain.

## 2. Fix

Immediate data fix:
```
dbt run --full-refresh -s daily_revenue
```
This restores Jan to 30 orders / 502.00 and Feb to 32 / 474.00.

To stop it recurring, make `daily_revenue` a plain table. It aggregates about 105 orders, so incremental buys nothing, and the lookback window is also wrong for late payments and backfills.

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

If it has to stay incremental as data grows, drop the `max(order_date)` window and key the lookback on a load timestamp from ingestion (e.g. `_loaded_at`). Then late-arriving old dates are rebuilt, and an occasional scheduled `--full-refresh` backs it up.

## 3. Test

A singular test, `tests/assert_daily_revenue_matches_orders.sql`, that reconciles the rollup to its source:

```sql
with o as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as expected_orders,
    d.orders as actual_orders,
    o.revenue as expected_revenue,
    d.revenue as actual_revenue

from o
full outer join {{ ref('daily_revenue') }} as d
    on o.order_date = d.order_date

where coalesce(o.orders, 0) <> coalesce(d.orders, 0)
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Against last night's state, it would have returned Jan 31 and the five February days (1, 3, 8, 13 and 22/26, minus the one that moved to Jan 31), so the build would have failed. It also catches any future drift between the two models.

I haven't run any of this, as I only had the pasted files and output, not the project itself.
