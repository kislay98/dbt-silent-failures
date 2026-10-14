claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 25 s | USD 0.066 | 2026-10-14 | project bef6b69

## 1. Root cause

**`models/daily_revenue.sql`** is incremental (`delete+insert`) and only recomputes `order_date >= max(order_date) - 3 days`. Its comment assumes payments land "a day or two after the order". They don't.

Some payments reached `raw_payments` weeks after the order. In `stg_payments`, orders 30, 33 and 36 each have an original payment (ids 35, 38, 41) plus a later one (ids 114, 115, 116), well past the original id range. `orders` is a full table rebuild, so it picks these up. `daily_revenue` has `max(order_date)` = 2018-04-09, so only 2018-04-06 onwards is rebuilt. February rows were frozen at their first-load values.

The numbers fit this:
- Order count is unchanged (27 on both sides), because only `sum(amount)` is stale.
- Every mismatched day has `orders_amount > daily_revenue`. The per-day gaps are 5, 12, 8, 15, 6, 9, 11 and 4, which sum to **70.00** (485 − 415).
- Order 30's late payment of 5.0 explains the 5.0 gap on 02-02, and order 33's 12.0 explains the 12.0 gap on 02-04.
- January and March agree because no late payments landed on those orders. These appear to be a one-off late February load, but I can't confirm that without the seed data.

All 40 tests pass because nothing compares `daily_revenue` to `orders`. `unique` and `not_null` on a stale table are still true.

## 2. Fix

`raw_payments` has no `loaded_at` column, so there is nothing to build a correct incremental window on. The aggregate is tiny, so drop the incremental logic.

```sql
-- models/daily_revenue.sql
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

With the default `+materialized: table`, this rebuilds fully on every run. For immediate repair of the existing table:

```
dbt run --full-refresh -s daily_revenue
```

If the table ever gets big enough to need incremental, add an arrival timestamp to the payments feed and filter on that. Otherwise, schedule a periodic full refresh. Don't rely on a fixed date lookback.

After the rebuild, February should show 485.00 and match settlement.

## 3. Test that would have caught it

A singular test, `tests/assert_daily_revenue_matches_orders.sql`:

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
    o.orders as expected_orders, d.orders as actual_orders,
    o.revenue as expected_revenue, d.revenue as actual_revenue
from o
full outer join d on o.order_date = d.order_date
where coalesce(o.orders, 0) != coalesce(d.orders, 0)
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

On the current state this returns the eight February dates above. A cheaper check is `dbt_utils.equality` or `expression_is_true`, but this one reports which days drifted.

## Side note (not verified)

`raw_customer_regions` is described as one row per customer and has an `updated_at` column. Nothing tests `customer_id` for uniqueness. If a customer has multiple history rows, `region_revenue` will double-count their revenue in the join. It doesn't explain the February gap, but it's worth a `unique` test on `customer_id`.
