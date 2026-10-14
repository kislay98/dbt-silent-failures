claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 35 s | USD 0.006 | 2026-10-14 | project 8ee5734 | prompt 89287ed414a2

## 1. Root cause

`daily_revenue` is an incremental model, and its incremental filter can't see the backfilled orders.

- **File:** `jaffle_shop/models/daily_revenue.sql`, the `is_incremental()` block:
  ```sql
  where order_date >= (select max(order_date) - interval 3 day from {{ this }})
  ```
  Each run deletes and reinserts only the last three days of `daily_revenue`. The comment assumes late data means payments landing a day or two after the order. The backfill is different: six orders dated in February arrived in last night's load. February is far outside the window, so those rows never reach `daily_revenue`.

- **Data evidence:** `orders` is a full `table` model, so it picked up the backfill. `daily_revenue` kept its old February rows. The mismatch query shows the gaps on exactly six February orders:

  | Date | Missing orders | Missing amount |
  |---|---|---|
  | 02-03 | 1 | 12.00 |
  | 02-08 | 1 | 9.00 |
  | 02-13 | 1 | 15.00 |
  | 02-17 | 1 | 6.00 |
  | 02-22 | 1 | 20.00 |
  | 02-26 | 1 | 3.00 |
  | **Total** | **6** | **65.00** |

  That matches the backfill exactly (33 − 27 = 6 orders, 480.00 − 415.00 = 65.00). March and April match, since they fall inside the window.

- **Why the tests passed:** The `daily_revenue` tests are `unique` and `not_null` on `order_date`, `orders`, and `revenue`. Those are per-row checks. Nothing compares `daily_revenue` to its source, so a missing row can't fail them.

**A separate defect I found while checking the output:** `region_revenue` totals 1838.00 in revenue, but `orders` totals 1737.00. The 101.00 excess is most likely fan-out in `region_revenue.sql`. `raw_customer_regions` has an `updated_at` column, which suggests multiple rows per customer, and the join on `customer_id` multiplies payments. The schema only tests `customer_id` for `not_null`, not `unique`. I can't see the seed data, so I haven't confirmed this. Check with `select count(*) - count(distinct customer_id) from raw_customer_regions`.

## 2. Fix

**Make `daily_revenue` a full rebuild.** The source can backfill any date, so an incremental window can't be correct. The model is a single group-by over `orders`, so a table rebuild is cheap.

`jaffle_shop/models/daily_revenue.sql`:

```sql
{{
    config(
        materialized='table'
    )
}}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    -- full rebuild: upstream backfills can land on any historical date
    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

Also update the description in `jaffle_shop/models/schema.yml`. It currently says "Incremental, rebuilds the last three days on each run", which will no longer be true.

Then run `dbt run --select daily_revenue`. This is the data fix; the table is rebuilt from `orders` and the February rows come back.

If the table grows large enough that a full rebuild is too slow, switch back to incremental. The window then has to be based on when a row was loaded, which needs a load timestamp from the ingestion job. `ordered_at` won't work for backfills.

**For the `region_revenue` fan-out**, dedupe `raw_customer_regions` to one row per customer (latest `updated_at`) in a staging model, and join that instead of the raw seed.

## 3. Test that would have caught this

Add a singular test that reconciles `daily_revenue` against `orders`. Save it as `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`:

```sql
with expected as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

),

actual as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders

from expected
full outer join actual
    on expected.order_date = actual.order_date

where coalesce(expected.orders, 0) <> coalesce(actual.orders, 0)
   or abs(coalesce(expected.revenue, 0) - coalesce(actual.revenue, 0)) > 0.005
```

A singular test passes when it returns zero rows. Against the current data, this query returns the six February dates above, so `dbt build` would have failed the night the backfill landed. If `dbt_utils` is installed, `equal_rowcount` and `equality` on `daily_revenue` give the same protection.

For `region_revenue`, add a reconciliation test too: `sum(revenue)` in `region_revenue` should equal `sum(amount)` in `orders`. It would fail now and catch the fan-out.

I haven't run any of this, since I only have the files and output you pasted. Running `dbt build` and the test query will confirm the diagnosis.
