claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 27 s | USD 0.179 | 2026-10-14 | project ad0b16b | prompt 6c2f9fd42779

# Root cause: `daily_revenue` skipped the February backfill, and the raw data is fine

## 1. Root cause

The bug is in **`models/daily_revenue.sql`**, in its incremental filter:

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
{% endif %}
```

On each incremental run, the model only rebuilds order dates within 3 days of the latest date already in the table. That latest date is in April 2018 (April 3–7), so the window covers only early April. The six backfilled orders (ids 100–105, e.g. 102 on 2018-02-13, 103 on 2018-02-17, 105 on 2018-02-26) have February order dates. They are in `raw_orders`, `stg_orders` and `orders`, but they fall outside the window. The `delete+insert` never touched their dates, so those February rows in `daily_revenue` still hold the values from before the backfill.

The numbers confirm this:
- `orders` shows February as 33 orders and 480.00. `daily_revenue` shows 27 and 415.00.
- The gap is exactly the six orders and 65.00 that ops described.
- January, March and April match exactly.
- `customers` (105 orders) and the `orders` status totals also include the backfill. Only the incremental model is stale.

The 3-day lookback was designed for payments landing a day or two late (see the comment in the model and its description in `schema.yml`). It assumes orders always arrive in `order_date` order. A store backfill breaks that assumption.

Every test passed because none of them compares `daily_revenue` with `orders`. The `unique` and `not_null` checks on stale rows still pass.

## 2. Fix

**Immediate repair** (rebuilds every date from `orders`):

```bash
dbt build --select daily_revenue --full-refresh
```

**Durable fix.** `orders` has about 105 rows, and the model already reads all of it. Incrementality saves nothing here, so the simplest correct option is to make the model a plain table:

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

If it has to stay incremental, base the filter on what changed, not on a fixed date window. For example, also rebuild any date whose totals no longer match `orders`:

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
   or order_date in (
       select o.order_date
       from (select order_date, count(*) as orders, sum(amount) as revenue
             from orders group by order_date) o
       left join {{ this }} t on t.order_date = o.order_date
       where t.order_date is null
          or t.orders <> o.orders
          or abs(coalesce(t.revenue, 0) - coalesce(o.revenue, 0)) > 0.005
   )
{% endif %}
```

At larger scale, the proper version is to have ingestion add a `_loaded_at` column to `raw_orders` and `raw_payments`. The model would then rebuild the `order_date`s of rows loaded since the last run. The `raw_orders` table has no such column today.

## 3. Test that would have caught it

Add a singular test, `tests/assert_daily_revenue_matches_orders.sql`. It returns rows (and so fails) whenever any date disagrees:

```sql
with expected as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

),

actual as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(e.order_date, a.order_date) as order_date,
    e.orders  as expected_orders,
    a.orders  as actual_orders,
    e.revenue as expected_revenue,
    a.revenue as actual_revenue
from expected e
full outer join actual a
    on e.order_date = a.order_date
where e.order_date is null
   or a.order_date is null
   or e.orders <> a.orders
   or abs(coalesce(e.revenue, 0) - coalesce(a.revenue, 0)) > 0.005
```

Last night this test would have failed on the February dates with the backfilled orders (13th, 17th, 26th and the others). `dbt build` would then have reported an error.
