claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 18 s | USD 0.084 | 2026-10-14 | project ad0b16b | prompt 6c2f9fd42779

## 1. Root cause

`models/daily_revenue.sql`: the incremental filter is keyed on the order date, not on when the data arrived:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

On each run it only recomputes the last 3 days before the table's latest `order_date` (early April here). The model assumes late data lands "a day or two after the order". The backfill from the store whose sync was down is different: it arrived last night carrying February order dates.

- `orders` is a full-rebuild table, so it picked up the six completed orders and shows 33 orders / 480.00 for February.
- `daily_revenue` never re-aggregated those February days, so it still shows 27 / 415.00.
- The gap is exactly 6 orders and 65.00, which matches the backfill.

All tests pass because none of them compares the two models. `unique` and `not_null` on `daily_revenue` are satisfied by a stale table.

## 2. Fix

Immediate data fix, rebuilding the table from `orders`:

```
dbt build --full-refresh -s daily_revenue
```

Durable fix: with about 100 orders there's no reason to be incremental, and any fixed lookback will break on the next large backfill. Make it a plain table in `daily_revenue.sql`:

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

If the model has to stay incremental at larger volume, the window needs to key on load time (for example an `_loaded_at` column on the raw tables). `order_date` can't detect late arrivals. A periodic `--full-refresh` would also be needed.

## 3. Test that would have caught it

A singular test, `tests/assert_daily_revenue_matches_orders.sql`, that reconciles the two models per day:

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

where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

Last night it would have returned the February days with the missing orders and failed the build.

## Separate bug in the output

`region_revenue` totals 1,838.00, but total order amount is 1,737.00. `raw_customer_regions` has 106 rows for 100 customers (it carries `updated_at`), so some customers have multiple region rows. The `join regions` fans out and double-counts their payments. That model should dedupe to the latest row per `customer_id`, and the `raw_customer_regions.customer_id` column should get a `unique` test (which currently only has `not_null`).
