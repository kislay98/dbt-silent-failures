claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 22 s | USD 0.092 | 2026-10-14 | project ad0b16b | prompt 0a4fd749d3e5

## 1. Root cause

**File:** `models/daily_revenue.sql`, the `is_incremental()` block:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`orders` is a full-refresh table, so it is rebuilt from the seeds on every run. `daily_revenue` only recomputes the last three days, anchored to its own `max(order_date)` (early April here). Every earlier date is frozen at whatever it was when it was last written.

The data shows exactly this:
- Only February differs: 33 orders / 480.00 in `orders` against 27 orders / 415.00 in `daily_revenue`. January, March and April match.
- The gap is 6 orders and 65.00, which equals the overall gap (1737 − 1672).
- `customers` (1737) and `region_revenue` (sums to 1737) agree with `orders`, so the upstream data is consistent and only `daily_revenue` is stale.

So 6 February orders (and their payments) reached `raw_orders`/`raw_payments` after `daily_revenue` had already written February. That could be a backfill or late-landing payments more than 3 days after the order. They are far outside the 3-day window, so they are never picked up.

Nothing failed because every test checks `daily_revenue` in isolation (`unique`, `not_null`). A stale but well-formed row passes all of them. The comment in the model, "payments can land a day or two after the order", is the assumption that broke.

I can't see the seed rows, so I can't name the specific 6 order IDs. This query will list them:

```sql
select o.order_id, o.order_date, o.amount
from orders o
where o.order_date between '2018-02-01' and '2018-02-28'
  and o.order_date < (select max(order_date) - interval 3 day from daily_revenue)
-- compare against per-date totals in daily_revenue to see which dates are short
```

## 2. Fix

Repair the existing data right away:

```
dbt run --full-refresh -s daily_revenue
```

Then stop it from recurring. The model reads 105 rows, so incrementality buys nothing. The simplest durable fix is to drop it:

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

If it needs to stay incremental because the data will grow, widen the lookback to cover the real lateness, for example 90 days via `var('daily_revenue_lookback_days', 90)`. Also schedule a periodic `--full-refresh`. Any fixed window will eventually miss a late arrival that exceeds it.

## 3. Test that would have caught it

Add `tests/assert_daily_revenue_matches_orders.sql`, a singular test that reconciles the two by date:

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
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Against last night's output this would have returned the short February dates and failed the build. Per-date comparison pinpoints which days drifted, which a total-only check would not.
