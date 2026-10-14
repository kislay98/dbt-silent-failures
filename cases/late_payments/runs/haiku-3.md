claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 28 s | USD 0.005 | 2026-10-14 | project bef6b69

## 1. Root cause

**File:** `jaffle_shop/models/daily_revenue.sql`

`daily_revenue` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. The table's max date is 2018-04-09, so each run rebuilds only April 6 onward. February is never rebuilt. Its revenue is frozen at whatever the payments were when February was last computed, and later payments never reach it.

`orders` is a table that rebuilds fully on every run, so it picks up late payments. The data shows this:

- **Same orders on both sides.** The daily order counts match on every day, so the order set is identical. Only the amounts differ.
- **The dashboard is always short, never over.** On every February day that differs, `daily_revenue` is lower than `orders`. That matches payments that arrived after the February rows were built. The differences sum to 70.00 (485.00 − 415.00), which is the whole gap.
- **`orders` matches settlement.** `orders` totals 485.00, the processor settlement, so `orders` is correct and the dashboard is stale.
- **Late payments are visible.** Payment ids 114–116 (orders 30, 33, 36) are the highest ids, which is consistent with late loads. The raw table has no load timestamp, so I can't confirm their arrival dates from the data provided.

The model's comment ("payments can land a day or two after the order") is wrong for this data. Payments for February orders were landing weeks later, outside the 3-day window. The `schema.yml` description repeats the same assumption.

The existing tests can't catch this. `unique` and `not_null` never compare revenue to `orders`, so they pass.

Note: I diagnosed this from the log, the sample outputs, and the file contents. I didn't run any queries.

## 2. Fix

Make `daily_revenue` a full rebuild. The table is small (about 40 days), and `orders` is already a full table, so the incremental optimisation isn't needed.

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

Also update the description in `jaffle_shop/models/schema.yml`. Remove "Incremental, rebuilds the last three days on each run" and replace it with "Full rebuild from orders."

After merging, run `dbt build --select daily_revenue` (a table model rebuilds on every run, so no `--full-refresh` is needed). Then re-run the February comparison query to confirm it returns 485.00.

If you must keep it incremental for cost reasons, the window has to cover the real payment lag. Better still, compare against `orders` and rebuild any day whose totals differ. A fixed 3-day window will keep missing late payments.

## 3. A test that would have caught this

Add a singular test that reconciles `daily_revenue` against `orders` across all history. It must cover all dates, not just the recent window. A windowed check would miss February again.

`jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`:

```sql
with orders_by_day as (

    select
        order_date,
        round(sum(amount), 2) as expected_revenue

    from {{ ref('orders') }}

    group by order_date

),

daily as (

    select
        order_date,
        round(revenue, 2) as actual_revenue

    from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.expected_revenue,
    d.actual_revenue

from orders_by_day o

full outer join daily d
    on o.order_date = d.order_date

where o.expected_revenue is distinct from d.actual_revenue
```

This test returns a row for every day that disagrees, including days missing from either side. On the current data it would fail for February 2, 4, 10, 11, 16, 17, 21 and 26, and it would have failed on the first night the late payments landed.

Other things I noticed but did not verify:

- `region_revenue` joins `raw_customer_regions`, which has an `updated_at` column. If a customer has more than one row, revenue is double-counted. The `unique` test on `region` wouldn't catch this. Consider deduplicating the seed to one row per customer, or adding a test that `customer_id` is unique in the regions feed.
- `schema.yml` documents `total_order_amount` on `customers`, but `customers.sql` doesn't produce that column.
