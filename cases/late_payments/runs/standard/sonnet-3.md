claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 22 s | USD 0.087 | 2026-10-14 | project ad0b16b | prompt 5e8b9b5cd1d1

## 1. Root cause

The bug is in `models/daily_revenue.sql`, and `orders` is correct. `orders` ties to the processor settlement (485.00), and region and customer totals reconcile with it (1742.00 each).

`daily_revenue` is incremental (`delete+insert`) and only rebuilds `order_date >= max(order_date) - 3 days`. The newest order is in April, so the window covers roughly the first week of April and nothing earlier. Any row for an older date is frozen at whatever it held when it was first built.

The 27 orders match on both sides but the revenue is 70.00 short. That pattern points to this sequence:
- Some February orders were first loaded while their payments were missing or partial. `count(*)` still counted the orders, and `sum(amount)` ignored the NULLs.
- The payments landed later, and `orders` picked them up on its full rebuild.
- `daily_revenue` never revisited those February dates.

The code comment says late payments are handled by the 3-day lookback. That only works if the lag is under 3 days and the date is still the latest one in the table. Here, payments arrived after February fell out of the window.

Every dbt test passes because none of them compares `daily_revenue` to `orders`. They only check that `order_date` is unique and the columns are not null.

I can't see the table's load history, so the late-payment explanation is inferred. This query confirms it and shows which February days are stale:

```sql
select o.order_date, o.revenue as orders_rev, d.revenue as daily_rev
from (select order_date, sum(amount) revenue from orders group by 1) o
left join daily_revenue d using (order_date)
where coalesce(o.revenue,0) <> coalesce(d.revenue,0);
```

## 2. Fix

Immediate data fix:

```
dbt run --full-refresh -s daily_revenue
```

February should then show 485.00.

Lasting fix: this model is a cheap aggregate over a 99-row table. The simplest option is to drop the incremental config and let it use the project default (`table`):

```sql
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

If the table becomes too large to rebuild, keep it incremental. Then anchor the lookback to the run date and size it to the real payment lag, for example `where order_date >= current_date - interval 60 day`. Add a periodic full refresh as well.

## 3. Test that would have caught it

Add `tests/assert_daily_revenue_matches_orders.sql`, a singular test that returns rows on any per-date mismatch:

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
    o.orders as orders_orders,
    d.orders as daily_orders,
    o.revenue as orders_revenue,
    d.revenue as daily_revenue
from o
full join d on o.order_date = d.order_date
where coalesce(o.orders, 0) <> coalesce(d.orders, 0)
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

It compares revenue per date, not just row counts, which is why it would have caught this. Row counts matched, so a count-only check would have passed.

Optionally, add a second singular test that `sum(amount)` from `orders` equals `sum(amount)` from `stg_payments`. That checks the whole chain against the raw payments.
