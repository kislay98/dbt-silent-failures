claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 29 s | USD 0.137 | 2026-10-14 | project 8ee5734 | prompt 89287ed414a2

# Root cause: February dates in `daily_revenue` were never recomputed after the backfill

## 1. Root cause

**File:** `jaffle_shop/models/daily_revenue.sql`, the `is_incremental()` filter.

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

On each run, the model only recomputes the last three `order_date`s that are already in the table. That window is in April 2018. The backfilled store's six orders arrived in `raw_orders` last night, but their `ordered_at` dates are in February. They flow through `stg_orders`, which is a view, and into `orders`, which is a full table rebuild, so `orders` has them. `daily_revenue` never looks at February again, so those dates keep the values from before the backfill.

The diff query matches the backfill exactly:

| order_date | missing orders | missing amount |
|---|---|---|
| 2018-02-03 | 1 | 12.00 |
| 2018-02-08 | 1 | 9.00 |
| 2018-02-13 | 1 | 15.00 |
| 2018-02-17 | 1 | 6.00 |
| 2018-02-22 | 1 | 20.00 |
| 2018-02-26 | 1 | 3.00 |
| **Total** | **6** | **65.00** |

That is 480 − 415 = 65 and 33 − 27 = 6. The `customers` sample agrees: customers 2, 15, 28, 41, 60 and 77 each have one order on these dates.

The data itself is correct. The incremental logic assumes late data only lands within three days of its `order_date`, and a store backfill breaks that assumption. `raw_orders` has no load timestamp, so the model can't detect late arrivals. Every existing test checks one table at a time, which is why the build passed.

## 2. Fix

**Immediate repair:**
```bash
dbt build --select daily_revenue --full-refresh
```

**Durable fix:** stop making it incremental. `orders` and everything upstream of it are already fully rebuilt on every run. `daily_revenue` is about 100 dates aggregated from that table, so the incremental setup saves nothing and only creates this risk.

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

If it has to stay incremental, the ingestion job needs to add a `loaded_at` column to `raw_orders`. The filter should then be on `loaded_at > max(loaded_at) in {{ this }}`, rebuilding every `order_date` touched by newly loaded rows rather than a fixed date window. Also update the model description in `schema.yml`, which still says "rebuilds the last three days".

## 3. Test that would have caught it

`tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must agree with orders for every order date
with expected as (

    select
        order_date,
        count(*) as orders,
        coalesce(sum(amount), 0) as revenue
    from {{ ref('orders') }}
    group by order_date

),

actual as (

    select order_date, orders, coalesce(revenue, 0) as revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue
from expected
full outer join actual
    on expected.order_date = actual.order_date
where expected.order_date is null
   or actual.order_date is null
   or expected.orders <> actual.orders
   or abs(expected.revenue - actual.revenue) > 0.005
```

Last night this would have returned the six February rows and failed the build.

## Separate issue: `region_revenue` is inflated

The `region_revenue` output shows a second, unrelated problem. It sums to **1838.00**, but `orders` totals **1737.00**, so revenue is overstated by 101.00.

`raw_customer_regions` has an `updated_at` column, which suggests it keeps history with several rows per customer. Joining on `customer_id` would then duplicate payments. The seed has no `unique` test on `customer_id`, so nothing flagged it.

To fix it:
- Dedupe to the latest row per customer, for example `qualify row_number() over (partition by customer_id order by updated_at desc) = 1`.
- Add a `unique` test on `raw_customer_regions.customer_id`.
- Add a reconciliation test like the one above comparing `region_revenue` against `orders`.
