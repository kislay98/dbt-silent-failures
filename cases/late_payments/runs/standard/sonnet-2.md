claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 20 s | USD 0.085 | 2026-10-14 | project ad0b16b | prompt 5e8b9b5cd1d1

## 1. Root cause

`models/daily_revenue.sql` is the problem. It is incremental with `delete+insert`, and on each run it only rebuilds `order_date >= max(order_date) - 3 days`. The latest order date in the data is in April 2018, so only the last three days are ever recomputed.

The comment in the model says payments land a day or two after the order. February orders clearly got payments (or payment amounts) well after that window had moved on. Those rows reached `raw_payments` after daily_revenue's February rows had been written, so `daily_revenue` kept the stale values:

- `orders` is a full table rebuild every night, so it picked up the late payments: 485.00, which matches the processor.
- The February rows in `daily_revenue` were never deleted and re-inserted, so they stayed at 415.00. The missing 70.00 is the payments that arrived late.
- The order count is still 27 because `count(*)` counts orders whether or not they had payments yet, while `sum(amount)` skips nulls. That is why only revenue differs.
- January and March agree because their payments were all present when the rows were first built.

The tests all pass because `daily_revenue` only has `unique`/`not_null` tests. Nothing compares it to `orders`. `not_null` on `revenue` can't see a value that is wrong but present.

I haven't seen per-day data, so this is inferred from the model logic and the numbers. To confirm, run this and expect the 70.00 gap to show up on specific February dates:

```sql
select o.order_date, o.revenue, d.revenue as daily_revenue
from (select order_date, sum(amount) revenue from orders group by 1) o
join daily_revenue d using (order_date)
where o.revenue <> d.revenue
```

## 2. Fix

Immediate: rebuild the stale rows.

```
dbt run --full-refresh -s daily_revenue
```

Durable: the three-day lookback is the wrong tool here. The table is a 69-row aggregate over 99 orders, so incrementality buys nothing. Simplest and safest is to drop the incremental config:

```sql
{{ config(materialized='table') }}
```

and remove the `is_incremental()` block. If it must stay incremental at scale, the window has to follow payment arrival, not order date. That means adding a `loaded_at` to `raw_payments` in ingestion and reprocessing every order_date touched by payments loaded since the last run. The raw table currently has no such column, which is why the date-based guess was used.

## 3. Test

A reconciliation test comparing the dashboard table to its source, at the day grain (a monthly total could hide offsetting errors). Save as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with expected as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select
    coalesce(e.order_date, d.order_date) as order_date,
    e.orders as expected_orders, d.orders as actual_orders,
    e.revenue as expected_revenue, d.revenue as actual_revenue
from expected e
full outer join {{ ref('daily_revenue') }} d
    on e.order_date = d.order_date
where e.order_date is null
   or d.order_date is null
   or e.orders <> d.orders
   or abs(coalesce(e.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

On the current state this would have failed for the February dates. A cheap companion is a check of total `orders.amount` against the processor settlement, for example via a seed of settlement totals per month.
