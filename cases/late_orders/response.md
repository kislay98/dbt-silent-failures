## 1. Root cause

**`models/daily_revenue.sql`**: the incremental filter is anchored to the wrong thing.

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The "last three days" are measured from the latest `order_date` already in `daily_revenue`, not from when the data arrived. By now that max is in April, so only the last few April dates are ever rebuilt. A February order that lands in `raw_orders` or `raw_payments` late, with an `ordered_at` still in February, falls outside that window. It is never picked up, and nothing ever rebuilds those February dates.

The output data fits this:
- `orders` and `daily_revenue` differ only on six February dates (02-03, 02-08, 02-13, 02-17, 02-22, 02-26).
- On 02-03 and 02-22 the date has no row at all in `daily_revenue`.
- On the other four dates the row exists but is short by one order. The day was computed earlier, before the late order arrived.
- The gap is 6 orders (105 vs 99) and 65.00 in revenue (1737 − 1672 = 65; 480 − 415 = 65), all in February.
- January, March and April match exactly.

Why nothing failed: `daily_revenue`'s tests (`unique`, `not_null`) only check its own shape. Nothing compares it to `orders`, so the lost rows are silent. The `dbt build` log also shows only a normal incremental run.

I can't see the seed files, so I haven't confirmed which specific raw rows are late. The pattern above points strongly at late-arriving February orders, and you can confirm it by comparing the six dates against `raw_orders`.

## 2. Fix

`daily_revenue` is a ~100-row aggregate, so incremental buys nothing and costs correctness. The simplest fix is to make it a table in `models/daily_revenue.sql`:

```sql
{{ config(materialized='table') }}
```

Remove the `unique_key`, the `incremental_strategy`, and the `{% if is_incremental() %}…{% endif %}` block. Then rebuild once so the stale table is replaced:

```
dbt run --full-refresh -s daily_revenue
```

If you need to keep it incremental later, the lookback must be based on load time, not on the max `order_date`. That means a `loaded_at` column on the source, or a fixed lookback such as the last N days of today's date, plus a periodic full refresh.

## 3. Test that would have caught it

A reconciliation test at `tests/assert_daily_revenue_matches_orders.sql`. It returns rows on any date where the two models disagree, and it passes on a clean full refresh but fails on a drifted incremental table:

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
    o.orders as expected_orders,
    d.orders as actual_orders,
    o.revenue as expected_revenue,
    d.revenue as actual_revenue
from o
full outer join d on o.order_date = d.order_date
where coalesce(o.orders, -1) <> coalesce(d.orders, -1)
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

The `full outer join` also catches dates that exist in only one table, which covers the two February dates that were missing entirely.

One side note: `region_revenue` joins to `raw_customer_regions`, which has an `updated_at` column but only a `not_null` test on `customer_id`. If a customer ever appears twice there, revenue will silently double-count, so add `unique` on `customer_id`. That is not the cause of this discrepancy.
