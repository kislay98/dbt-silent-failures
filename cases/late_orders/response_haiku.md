## 1. Root cause

The problem is in `jaffle_shop/models/daily_revenue.sql`, in the `is_incremental()` filter:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`daily_revenue` is incremental with `delete+insert` on `order_date`, so each run only rebuilds the last three days. That window is anchored to the newest date in the table, which is April 2018. February is far outside it, so February rows are never revisited.

The output shows February was built from an incomplete set of orders and has never been corrected:

- The mismatched dates are scattered across February (2018-02-03, 02-08, 02-13, 02-17, 02-22, 02-26). On 02-03 `daily_revenue` has no row at all. On 02-08 it has 1 order where `orders` has 2. These are partial snapshots, not a single bad calculation.
- The gap is exactly six orders and 65.00 in revenue (33 vs 27 orders, 480.00 vs 415.00). That accounts for the whole difference: 1737 − 1672 = 65 and 105 − 99 = 6.
- January, March, and April match exactly, which fits a table where only February is stale.

The most likely history is that some February orders were not yet in `raw_orders` when the table was first built or last changed, and the orders arrived later. `orders` is a full rebuild each run, so it picked them up. `daily_revenue` never will, because nothing is outside the three-day window that rebuilds February. The comment about payments landing late is also misleading: `orders` already absorbs late payments on every run, so `daily_revenue` does not need its own lookback.

Nothing failed because no test compares `daily_revenue` to its source. The tests only check `unique` and `not_null` on `order_date`, `orders`, and `revenue`, and all of those still hold for the stale rows.

I reached this from the outputs you provided. I did not run any queries against the warehouse, so the exact history of the Feb rows is an inference. The mechanism itself is certain from the code.

Two things I ruled out or noted but did not treat as the cause:
- `raw_customer_regions` has an `updated_at` column, which suggests it may hold more than one row per customer. If it does, `region_revenue` fans out and double-counts. It does not affect `daily_revenue`, but it is worth checking separately.
- `customers` documents `total_order_amount`, which does not exist in the model. This is doc drift, not a data error.

## 2. Fix

Make `daily_revenue` a plain table. It is a small aggregate over `orders`, which is already rebuilt in full each run, so the incremental logic adds the bug without saving meaningful work.

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

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

Then run `dbt run --select daily_revenue`. Changing the materialization makes dbt recreate the relation, which corrects February in one step.

If you want to keep it incremental for performance, you would need to run a `--full-refresh` once and widen the lookback to cover all history. That only defers the problem, so I would not do it.

I have not run this change. Please run it against your warehouse and confirm the February totals come back to 480.00 and 1737.00 overall.

## 3. Test that would have caught it

Add a singular test that reconciles `daily_revenue` against `orders` across every date. It has to use a full outer join so that a missing day is caught as well as a mismatched one.

`tests/assert_daily_revenue_matches_orders.sql`:

```sql
with orders_by_day as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

),

compared as (

    select
        coalesce(o.order_date, d.order_date) as order_date,
        coalesce(o.orders, 0) as expected_orders,
        coalesce(d.orders, 0) as actual_orders,
        coalesce(o.revenue, 0) as expected_revenue,
        coalesce(d.revenue, 0) as actual_revenue

    from orders_by_day o

    full outer join {{ ref('daily_revenue') }} d
        on o.order_date = d.order_date

)

select *
from compared
where expected_orders <> actual_orders
   or abs(expected_revenue - actual_revenue) > 0.005
```

Against last night's data this test returns the six February dates and fails the build. It runs in `dbt build` after `daily_revenue`, so it would have turned last night's run red instead of green.
