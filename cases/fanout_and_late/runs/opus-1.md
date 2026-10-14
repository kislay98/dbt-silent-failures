claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 30 s | USD 0.140 | 2026-10-14 | project 8ee5734 | prompt 89287ed414a2

## 1. Root cause

**File: `models/daily_revenue.sql`.** The incremental filter has a fixed lookback window, so it cannot pick up late-arriving rows:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

Each run rebuilds only the last three days before the newest `order_date` already in `daily_revenue`, which is in April. The store backfill that landed last night added six orders to `raw_orders` with **February** `ordered_at` timestamps. `orders` is a full `table` rebuild, so it picked them up. `daily_revenue` never re-selected those February dates, and its stored rows still hold the pre-backfill counts.

The diff accounts for all of it:

| date | missing orders | missing amount |
|---|---|---|
| 02-03 | 1 | 12.00 |
| 02-08 | 1 | 9.00 |
| 02-13 | 1 | 15.00 |
| 02-17 | 1 | 6.00 |
| 02-22 | 1 | 20.00 |
| 02-26 | 1 | 3.00 |
| **total** | **6** | **65.00** |

That is the backfill exactly: 33 − 27 = 6 orders and 480 − 415 = 65.00.

Every test passed because the tests on `daily_revenue` only check each table against itself (`unique`, `not_null`). Nothing compares it to `orders`.

There is nothing wrong with the backfilled data. The problem is that `raw_orders` has no load timestamp, so an incremental model keyed on `order_date` cannot tell when old dates have changed.

## 2. Fix

**Immediate repair:**

```bash
dbt build --select daily_revenue --full-refresh
```

**Durable fix:** stop making this model incremental. Its upstream `orders` is already rebuilt as a full table every night, and the aggregate is tiny, so the incremental logic saves nothing and only adds risk.

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

If it has to stay incremental, the filter must also rebuild any date whose aggregate no longer matches `orders`. `delete+insert` on `order_date` then replaces those days correctly:

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
   or order_date in (
        select src.order_date
        from (select order_date, count(*) as n, sum(amount) as r from orders group by 1) src
        left join {{ this }} t using (order_date)
        where t.order_date is null or t.orders <> src.n or t.revenue is distinct from src.r
   )
{% endif %}
```

A cleaner long-term alternative is to have ingestion add a `loaded_at` column to `raw_orders` and filter on that instead.

## 3. Test that would have caught it

Add this as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must agree with orders for every order_date
with expected as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select
    coalesce(e.order_date, d.order_date) as order_date,
    e.orders  as expected_orders,  d.orders  as actual_orders,
    e.revenue as expected_revenue, d.revenue as actual_revenue
from expected e
full outer join {{ ref('daily_revenue') }} d using (order_date)
where e.order_date is null
   or d.order_date is null
   or e.orders <> d.orders
   or abs(coalesce(e.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Last night this would have returned the six February rows and failed the build.

## Separate bug in the same sample

`region_revenue` totals **1838.00 / 105 orders**, but `orders` totals **1737.00**, so it overstates revenue by 101.00. `raw_customer_regions` has an `updated_at` column, which means it likely holds several rows per customer. The join in `models/region_revenue.sql` then fans out payments for any customer who has more than one region row.

The seed only has `not_null` tests, so nothing catches this. To fix it:
- Add `unique` on `raw_customer_regions.customer_id` so the duplication is detected.
- Join only the latest row per customer, for example with `qualify row_number() over (partition by customer_id order by updated_at desc) = 1`.

This bug is not what caused the February mismatch, but it is worth raising with ops as well.
