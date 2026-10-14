claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 32 s | USD 0.006 | 2026-10-14 | project ad0b16b | prompt 5e8b9b5cd1d1

## 1. Root cause

**`jaffle_shop/models/daily_revenue.sql`**, in the incremental filter:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The window is anchored to the newest `order_date` already in the target table, not to the current date or to when each order's payments arrived. The data runs into April, so the rebuild only touches roughly the last three days of April. February, and everything before it, is frozen at whatever value it had when it was last inside the window.

The February numbers fit this:

- `orders` is a full table rebuilt every night from `stg_payments`. Its February total is 485.00, which matches the processor settlement, so `orders` is correct.
- `daily_revenue` sums the same `orders.amount`, but its February rows were written earlier, when some February payments had not landed. The order count is still 27 because the order rows existed. Only the amounts are short, by 70.00.
- March agrees because its payments had already settled before the window passed it. January agrees for the same reason.

The model's own comment ("payments can land a day or two after the order") describes the failure mode, but the three-day lookback doesn't handle it. A payment that lands more than three days after the latest order date never reaches the table.

Things I checked and ruled out:
- **Integer division in `stg_payments`:** `amount / 100` is DuckDB true division, and the sample shows `double` amounts, so cents are not being truncated.
- **Fan-out in `orders`:** `raw_payment_methods` has 4 rows, one per method, so the join does not duplicate payments.

I can't confirm from these files which payment makes up the 70.00. `raw_payments` has no load timestamp, and the sample is 10 of 121 rows. To confirm, check the ingestion job's history for February payments that were loaded after the February rows were last built.

## 2. Fix

Materialize `daily_revenue` as a table. It has about 69 rows and reads from `orders`, which is already a full table, so the incremental logic saves nothing and causes this bug.

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

This removes the `unique_key`, the `delete+insert` strategy, and the `is_incremental()` block. The schema.yml description "Incremental, rebuilds the last three days on each run" also needs updating.

To repair February before the next nightly run, run `dbt run --select daily_revenue --full-refresh` once. Since the model becomes a table, the nightly build will also rebuild it from scratch from then on.

## 3. Test that would have caught it

The existing tests only check nulls and uniqueness on `daily_revenue`, so a wrong amount passes. Add a reconciliation test that compares each day in `daily_revenue` with `orders`. Save it as `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`:

```sql
with daily_revenue as (

    select * from {{ ref('daily_revenue') }}

),

orders_by_day as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

),

mismatches as (

    select
        coalesce(d.order_date, o.order_date) as order_date,
        d.orders as dashboard_orders,
        o.orders as ledger_orders,
        d.revenue as dashboard_revenue,
        o.revenue as ledger_revenue

    from daily_revenue d

    full outer join orders_by_day o
        on d.order_date = o.order_date

    where d.orders is distinct from o.orders
       or round(d.revenue, 2) is distinct from round(o.revenue, 2)

)

select * from mismatches
```

A singular test passes when it returns no rows. Run against the current warehouse, this test returns the February rows (dashboard 415.00, ledger 485.00) and fails the build. Since it runs in `dbt build`, the nightly job would have failed instead of reporting success.

## Smaller issues I noticed (not the cause)

- `raw_payment_methods` has no `unique` test on `payment_method`. A duplicate row there would double-count payments in `orders`. Add the test.
- `raw_customer_regions` has no `unique` test on `customer_id`. A customer listed twice would inflate `region_revenue`. Add the test.
- `customers` in `schema.yml` documents a `total_order_amount` column that the model doesn't produce.
