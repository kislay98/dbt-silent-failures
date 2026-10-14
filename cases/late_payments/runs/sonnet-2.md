claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 24 s | USD 0.067 | 2026-10-14 | project bef6b69

## 1. Root cause

**File: `models/daily_revenue.sql`.** The incremental filter only reprocesses the last three days relative to the newest date already in the table, so late payments for older orders never reach it.

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`max(order_date)` in `daily_revenue` is 2018-04-09, so every build only recomputes about 2018-04-06 onward. Any February row is frozen at whatever it held on the day it was first built.

**What is wrong in the data:** `raw_payments` received payments after the orders were first built. The `stg_payments` sample shows ids 114, 115 and 116, well above the 35–41 ids of the original payments for the same orders:

| order | original payment | late payment | `orders.amount` | `daily_revenue` contribution |
|---|---|---|---|---|
| 30 | 13 (id 35) | 5 (id 114) | 18 | 13 |
| 33 | 22 (id 38) | 12 (id 115) | 34 | 22 |
| 36 | 9 (id 41) | 8 (id 116) | 17 | 9 |

The `orders` table is a full-table rebuild, so it picks up the late payments. `daily_revenue` never sees them. The eight February days that disagree sum to 5+12+8+15+6+9+11+4 = **70.00**, which is exactly the gap. The order counts match because late payments don't add orders, only amounts. January and March agree only because no late payments landed there. The same pattern gives the 1742.00 vs 1672.00 gap overall, which is also 70.00.

I can only see the late payments for orders 30, 33 and 36 in the sample. I'd expect the other five days to be explained by ids 117 and above, but I haven't seen those rows.

**Why the tests passed:** `daily_revenue` is only tested for `unique` and `not_null`. Nothing compares it to `orders`. The code comment assumes payments land "a day or two after the order", but these arrived weeks later.

## 2. Fix

**Data repair (immediate):**
```
dbt run --full-refresh -s daily_revenue
```
February then returns to 485.00, matching the processor settlement.

**Code fix:** make `daily_revenue` a plain table. It is a tiny aggregate over `orders`, and there is no reliable way to window it incrementally. `raw_payments` has no load or created timestamp, so you cannot tell which days changed.

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

Also update the model description in `schema.yml`, which currently says "Incremental, rebuilds the last three days on each run".

If it ever has to be incremental, the ingestion job must add a `loaded_at` column to `raw_payments`. The model can then rebuild every `order_date` touched by payments loaded since the last run.

## 3. Test that would have caught it

`tests/assert_daily_revenue_matches_orders.sql`:

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

where o.order_date is null
   or d.order_date is null
   or o.orders != d.orders
   or abs(o.revenue - d.revenue) > 0.005
```

On the pre-fix state this returns the eight February rows. After the fix it returns nothing, and it stays as a guard against anyone making the model incremental again. Add it with `severity: error` so a mismatch fails the nightly build.

## Side note

`raw_customer_regions` has an `updated_at` column, but the model and schema describe it as "one row per customer" and only test `not_null`. If it holds multiple versions per customer, `region_revenue` would fan out and overcount. I haven't seen the seed data, so I haven't verified this. It is worth adding a `unique` test on `customer_id`.
