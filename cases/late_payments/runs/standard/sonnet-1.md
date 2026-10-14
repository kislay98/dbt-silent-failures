claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 19 s | USD 0.084 | 2026-10-14 | project ad0b16b | prompt 5e8b9b5cd1d1

## 1. Root cause

`models/daily_revenue.sql` is the culprit. It is incremental with `delete+insert`, and on every run it only rebuilds `order_date >= max(order_date) - 3 days` from `{{ this }}`. That cutoff is anchored to the newest order date in the table (early April), not to when payments arrived.

Here is how I read the evidence:
- `orders` is correct. It joins payments live, and its February total of 485.00 matches the processor settlement.
- `daily_revenue` has the same 27 February orders but 70.00 less revenue. February was first materialised when some of its payments had not landed yet, so those orders were stored with partial amounts.
- When the payments arrived later, `raw_payments` and `orders` picked them up, but `daily_revenue` never rebuilt those February days because they sit far outside the 3-day window.
- The comment in the model says late payments are covered by the 3-day lookback. That only holds for orders in the last 3 days of the data.
- January and March agree, which suggests the late payments only touched February orders.
- Every test passes because none of them compares `daily_revenue` to `orders`. They only check `unique` and `not_null` on `daily_revenue`, and a stale sum is both.

I can't see the seed data, so I haven't identified which payment rows were late. Confirm with the diff query below, which should return the February dates adding up to 70.00.

## 2. Fix

First repair the data, then stop it recurring:

```
dbt run --full-refresh -s daily_revenue
```

To prevent a repeat, change the model to `materialized='table'` (or `view`). It is a 69-row aggregate over `orders`, so incremental saves nothing. `raw_payments` has no load or update timestamp, so there is no reliable high-water mark to base a correct incremental on.

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

Diff query to confirm the cause before the refresh:

```sql
select o.order_date, o.revenue as orders_revenue, d.revenue as daily_revenue
from (select order_date, sum(amount) as revenue from orders group by 1) o
join daily_revenue d using (order_date)
where o.revenue is distinct from d.revenue
```

## 3. Test that would have caught it

Add `tests/assert_daily_revenue_reconciles_to_orders.sql`, a singular test that fails if any date differs in either direction:

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
    o.orders as orders_orders, d.orders as daily_orders,
    o.revenue as orders_revenue, d.revenue as daily_revenue
from o
full outer join d on o.order_date = d.order_date
where o.order_date is null
   or d.order_date is null
   or o.orders != d.orders
   or abs(o.revenue - d.revenue) > 0.005
```

I'd also add a similar test on the grand totals of `orders.amount` and `stg_payments.amount`. Neither test would have passed with the stale incremental data.
