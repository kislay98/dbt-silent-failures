claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 32 s | USD 0.183 | 2026-10-14 | project ad0b16b | prompt 5e8b9b5cd1d1

## 1. Root cause

**The bug is in `models/daily_revenue.sql`, in its incremental filter. It is triggered by late-arriving rows in `raw_payments`.**

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The comment says payments can arrive a day or two after the order. But the lookback is anchored to the newest **order date** already in the table, not to when payments arrive. A day is recomputed only while it is within 3 days of the latest order date. After that it is frozen.

The data shows that some February payments arrived after their order dates had left that window:

- `orders` is a full `table` rebuild, so it picks up every payment and shows **485.00**, which matches the settlement.
- `daily_revenue` kept the February totals from the run where those order dates were last in the window, before the late payments existed. It shows **415.00**, which is 70.00 short.
- **Order counts agree (27 = 27).** The orders existed when those days were built. Only their payments were missing or partial. `count(*)` counted them, and `sum(amount)` skipped or under-counted them.
- **January and March agree.** All their payments arrived inside the window.
- **Nothing in the build failed.** Every `daily_revenue` test checks one column on one row (`unique`, `not_null`). None of them compares the table with its source.

`raw_payments` has no load timestamp, so the model cannot detect late arrivals. The loaded table has 121 payments, compared with 113 in the stock jaffle_shop seed, so the late rows are probably the highest `id`s. I can't confirm that from the sample. This query lists the affected days:

```sql
with expected as (
    select order_date, count(*) as orders, coalesce(sum(amount), 0) as revenue
    from orders group by 1
)
select e.order_date, e.orders, e.revenue as expected, d.revenue as actual, e.revenue - d.revenue as missing
from expected e join daily_revenue d using (order_date)
where e.revenue <> d.revenue
order by 1;
-- expect rows only in Feb 2018, with `missing` summing to 70.00
```

## 2. Fix

**Repair the data now:**

```bash
dbt build --select daily_revenue --full-refresh
```

**Fix the model.** The window can't be made correct without knowing when payments arrived. The table has about 69 rows, so the simplest correct option is to drop incremental and use a plain table:

```sql
{{ config(materialized='table') }}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        coalesce(sum(amount), 0) as revenue

    from orders

    group by order_date

)

select * from final
```

If it has to stay incremental, ingestion needs to add a `_loaded_at` column to `raw_payments`, and `stg_payments` needs to pass it through. The filter should then rebuild every order date that received a payment since the last run:

```sql
{% if is_incremental() %}
where order_date in (
    select o.order_date
    from {{ ref('stg_payments') }} p
    join {{ ref('stg_orders') }} o using (order_id)
    where p._loaded_at > (select max(_built_at) from {{ this }})
)
{% endif %}
```

This also requires adding `current_timestamp as _built_at` to the select.

## 3. Test that would have caught it

Add a reconciliation test at `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must equal orders aggregated by order_date
with expected as (

    select order_date, count(*) as orders, coalesce(sum(amount), 0) as revenue
    from {{ ref('orders') }}
    group by order_date

),

actual as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(e.order_date, a.order_date) as order_date,
    e.orders  as expected_orders,  a.orders  as actual_orders,
    e.revenue as expected_revenue, a.revenue as actual_revenue
from expected e
full outer join actual a on e.order_date = a.order_date
where a.order_date is null
   or e.order_date is null
   or e.orders <> a.orders
   or abs(e.revenue - a.revenue) > 0.005
```

On the current data this test fails on the February dates that are missing the 70.00. It also catches any later drift from incremental logic.
