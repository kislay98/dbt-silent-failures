claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 24 s | USD 0.065 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`models/daily_revenue.sql`**: the incremental filter only rebuilds a window anchored to the newest data, so it can never pick up late or backfilled rows for older dates:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`max(order_date)` in `{{ this }}` is in April, so each run only reprocesses early April. Any February order or payment that arrived after February was first processed is never revisited. The comment assumes late data lands "a day or two" after the order, but the window is tied to the latest order date in the table, not to when the data arrived.

The output tables support this. `orders` is rebuilt as a full table every run, so it's correct. `daily_revenue` is short by exactly 6 orders (105 vs 99) and 65.00 (1737 − 1672), all in February:
- `2018-02-03` and `2018-02-22` have no row at all, so those orders arrived after those dates were last built.
- `2018-02-08`, `02-13`, `02-17` and `02-26` each have one fewer order than `orders` shows, so one order per day arrived late.
- January, March and April match exactly.

Every test passes because none of them compare `daily_revenue` to `orders`. `unique`, `not_null` and `not_null` on the metrics all hold on a table that is silently missing rows. The build log shows "OK created" with no errors because nothing is technically wrong with the run.

I can't see the seed data, so the diagnosis is inferred from the reconciliation output. To confirm, check that the six missing orders in `raw_orders` have `ordered_at` in those February dates but were added after `daily_revenue` was last built.

## 2. Fix

Repair now: `dbt run --full-refresh -s daily_revenue`. This restores 480.00 for February and 1737.00 overall.

The incremental model needs to be removed to stop this recurring. At about 100 rows it gains nothing from being incremental, so make it a plain table in `daily_revenue.sql`:

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

If it has to stay incremental at larger scale, the window must key off a load or updated timestamp, which these raw tables don't have. Filtering on `order_date` can't catch backfills. A scheduled periodic full refresh would be the fallback.

## 3. Test that would have caught it

`tests/assert_daily_revenue_matches_orders.sql` is a singular test that reconciles per day, in both directions:

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
where coalesce(o.orders, 0) <> coalesce(d.orders, 0)
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

On the current data it would have returned the six February rows and failed the build.

A side risk I noticed in `region_revenue`: the seed test only checks `not_null` on `customer_id`, not `unique`. If `raw_customer_regions` has duplicate customers (it has an `updated_at` column, which hints at history), the join fans out and inflates revenue. Add `unique` on `customer_id` to that seed. It isn't the cause of this issue.
