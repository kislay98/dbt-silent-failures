claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 21 s | USD 0.089 | 2026-10-14 | project ad0b16b | prompt 0a4fd749d3e5

## 1. Root cause

**`models/daily_revenue.sql`** is the problem. The incremental filter only reprocesses the last three days, relative to the table's own max date:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`orders` is a full-refresh table, and the seeds are reloaded in full on every run. So `orders` always reflects the current raw data, while `daily_revenue` keeps whatever it had when each row was first written. The February gap is 6 orders (33 vs 27) and 65.00 (480 vs 415). Every other month matches exactly.

That fits late-arriving or backfilled February data in `raw_orders` or `raw_payments`. It landed after `daily_revenue` had built those dates, and it falls outside the 3-day window, so it was never picked up. The comment in the file, "payments can land a day or two after the order", describes the lateness the window was built for. It does not cover a backfill weeks later.

Every test passes because none of them compare `daily_revenue` to its source. `unique`, `not_null` and `not_null` still hold on a table that is stale but internally consistent.

I can't see the seed data, so I haven't confirmed which February dates are missing or short. Run this to confirm:

```sql
select o.order_date, o.n as orders_n, d.orders as daily_n, o.amt, d.revenue
from (select order_date, count(*) n, sum(amount) amt from orders group by 1) o
left join daily_revenue d using (order_date)
where d.order_date is null or o.n <> d.orders or o.amt is distinct from d.revenue
order by 1;
```

The `orders` and `customers` totals agree at 1737 and `region_revenue` sums to 1737, so the other models are fine.

## 2. Fix

Immediate: rebuild the table.

```
dbt run --full-refresh -s daily_revenue
```

Durable: the data is 105 orders, and a fixed lookback can't handle arbitrary backfills. Drop the incremental logic and make it a plain table:

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

If it must stay incremental at larger volume, use a much longer lookback or a `updated_at` high-water mark on the source. Schedule a periodic `--full-refresh` as well. Update the model description in `schema.yml`, which currently says it rebuilds the last three days.

## 3. Test that would have caught it

A singular test, `tests/assert_daily_revenue_matches_orders.sql`. It returns rows on any mismatch, so it fails the build:

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
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where o.order_date is null
   or d.order_date is null
   or o.orders <> d.orders
   or o.revenue is distinct from d.revenue
```

This compares per date, so it catches missing days and wrong values. A total-only check could hide offsetting errors. The `orders` model has no revenue-versus-source reconciliation either. You could add `dbt_utils.equal_rowcount` or a total check of `sum(amount)` against `stg_payments` as a second guard.
