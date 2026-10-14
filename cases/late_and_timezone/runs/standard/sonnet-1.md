claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 39 s | USD 0.111 | 2026-10-14 | project ad0b16b | prompt 150d20052204

## 1. Root cause

`models/daily_revenue.sql` is the cause. It is incremental, and its filter is relative to the table's own contents:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The model only reprocesses the last three days before the latest `order_date` already in `daily_revenue`, which is around 2018-04-07. The comment says this is for payments landing late, but the filter is on the *order's* date, not on when the data arrived.

Last night's backfill added completed orders dated 1, 3, 8, 13, 22 and 26 February. `orders` is a full-refresh table, so it picked them all up. `daily_revenue` never looks back that far, so it never rebuilt those days, and its old rows for them stay frozen.

The per-month totals in your output show the gap:

| Month | `orders` | `daily_revenue` | Gap |
|---|---|---|---|
| Feb | 32 / 474.00 | 27 / 415.00 | 5 orders / 59.00 |
| Jan | 30 / 502.00 | 29 / 496.00 | 1 order / 6.00 |

Together that is 6 orders and 65.00, which is exactly the backfill. One of the six backfilled orders (the 6.00 one) has a January `order_date` in `orders`, so the January gap is the same bug. Your ops reconciliation looks at February, so it only saw five of the six.

I can't see the seed rows, so I haven't confirmed which order that is. This query will show it:
```sql
select * from orders where order_id not in (select order_id from orders) -- placeholder: use the diff query in section 3
```
The diff test in section 3 returns the exact days, so use that.

Every test passed because they only check shape (unique, not null, accepted values). Nothing compares `daily_revenue` to `orders`.

## 2. Fix

Immediate data repair:
```
dbt run --full-refresh -s daily_revenue
```

Durable fix: with about 100 orders, the incremental model saves nothing, so make it a table in `models/daily_revenue.sql`:
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
Also update the model description in `schema.yml`, which still says "Incremental, rebuilds the last three days".

If the table grows enough to need incremental later, filter on an ingestion timestamp (`loaded_at` added by the ingestion job). Rebuild every `order_date` that has rows loaded since the last run, not the last N days of order dates.

## 3. Test that would have caught it

Add `tests/assert_daily_revenue_matches_orders.sql`:
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
   or e.orders <> d.orders
   or abs(e.revenue - d.revenue) > 0.005
```
This fails with one row per mismatched day after the backfill. It would have listed the January day and the five February days. It also works as a permanent reconciliation check if `daily_revenue` ever goes back to being incremental.

I haven't run any of this, since I only have the files and output you pasted.
