claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 31 s | USD 0.005 | 2026-10-14 | project 8ee5734 | prompt 89287ed414a2

## 1. Root cause

**`models/daily_revenue.sql`** is the broken file. `orders` is correct. It's a full-rebuild table, so it includes the six backfilled February orders (105 orders, 1737.0 total). `daily_revenue` is incremental, and its filter only rebuilds the last three days:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The window is anchored to the newest date in the table, which is April 2018 (the sample shows April rows). So the rebuild only touches late March onward. The backfilled orders are dated in February, outside the window, so the `delete+insert` never rewrites those rows. The February rows keep their pre-backfill values.

The mismatch query confirms this. The six days with gaps are all in February and sum to exactly the missing amount:

| date | missing orders | missing amount |
|---|---|---|
| 02-03 | 1 | 12.0 |
| 02-08 | 1 | 9.0 |
| 02-13 | 1 | 15.0 |
| 02-17 | 1 | 6.0 |
| 02-22 | 1 | 20.0 |
| 02-26 | 1 | 3.0 |

That is 6 orders and 65.00, which accounts for 27→33 and 415→480. Two of those dates (02-03, 02-22) have no `daily_revenue` row at all, so the backfill never produced one.

The three-day window was written for payments landing late, not for backfilled orders. Nothing in the tests catches this because the `daily_revenue` tests are only `unique` and `not_null`, and nothing compares it to `orders`. The log is also consistent with the incremental path: it's a 0.03s run with no full-refresh.

**Secondary issue (not the February problem):** `region_revenue` looks inflated. Its revenue sums to 1838.0, but `orders` totals 1737.0. Its order count (105) matches, but that is a `count(distinct)`, so the same shape of problem would not show there. The likely cause is duplicate customer rows in `raw_customer_regions`. The feed has an `updated_at` column, and the only test on `customer_id` is `not_null`, so a customer with two region rows fans out every payment. I can't confirm this from the output. Check with:

```sql
select customer_id, count(*) from raw_customer_regions group by 1 having count(*) > 1;
```

## 2. Fix

Rebuild the table as a plain table. It's a cheap aggregation over `orders`, and the incremental window is the source of the bug:

```sql
-- models/daily_revenue.sql
{{
    config(
        materialized='table'
    )
}}

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

Also update the description in `models/schema.yml` (it still says "Incremental, rebuilds the last three days"). If you need incremental for performance later, the window has to be keyed on when rows arrived, not on `order_date`. Until then, `dbt run --select daily_revenue --full-refresh` fixes the current table without a code change.

For the regions issue, dedupe `raw_customer_regions` to one row per customer (for example, the latest `updated_at`) in a staging model before joining, and add `unique` on `customer_id` to that seed's tests.

I haven't applied any of this. I only had the outputs you pasted, so the region-duplicate theory still needs the query above to confirm.

## 3. A test that would have caught it

Add a singular test that reconciles `daily_revenue` against `orders` for every date:

```sql
-- tests/assert_daily_revenue_matches_orders.sql
with orders as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

),

daily as (

    select * from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders,
    d.orders as daily_orders,
    o.revenue,
    d.revenue as daily_revenue

from orders o

full outer join daily d
    on o.order_date = d.order_date

where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

This fails on any date where the two disagree, including dates that exist on only one side. It would have failed on last night's build for the six February dates. A similar singular test that compares `sum(revenue)` in `region_revenue` to `sum(amount)` in `orders` would have caught the region fan-out.
