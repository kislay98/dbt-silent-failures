claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 36 s | USD 0.195 | 2026-10-14 | project ad0b16b | prompt 6c2f9fd42779

# Daily revenue is missing February's backfilled orders

## 1. Root cause

The bug is in `jaffle_shop/models/daily_revenue.sql`, specifically its incremental filter. The raw data and `orders` are correct.

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
{% endif %}
```

On each run, `daily_revenue` only recomputes order dates within 3 days of the latest date already in the table. The latest order is around 2018-04-07, so only dates from roughly 2018-04-04 onward get rebuilt.

The backfilled store's orders have February `order_date`s, for example order 102 on 2018-02-13, 103 on 2018-02-17 and 105 on 2018-02-26. Those dates are far outside the window. The `delete+insert` therefore never touches them, and the February rows in `daily_revenue` keep their pre-backfill values.

The numbers confirm this exactly:

- **Orders:** `orders` has 33 in February, and 27 + 6 backfilled = 33.
- **Revenue:** `orders` has 480.00, and 415.00 + 65.00 = 480.00.
- **Other months:** January, March and April match between the two tables.
- **Upstream counts:** `raw_orders`, `stg_orders` and `orders` all have 105 rows, so nothing was lost upstream.

The comment's assumption ("payments can land a day or two after the order") only covers short delays. It does not cover a backfill that arrives weeks late. All the tests passed because they only check `daily_revenue` on its own (unique and not-null). Nothing compares it to `orders`.

## 2. Fix

**Immediate repair (rebuilds the stale February rows):**

```bash
dbt build --select daily_revenue --full-refresh
```

**Durable fix:** the model aggregates about 105 orders into about 69 rows, so incremental logic isn't worth the risk. Make it a plain table:

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

If the model must stay incremental as volume grows, the filter has to key on *when rows arrived*, not on `order_date`. That means having ingestion add a `_loaded_at` column to `raw_orders` and `raw_payments`, then rebuilding every `order_date` that has rows loaded since the last run. A fixed lookback window on the business date will always miss backfills.

## 3. Test that would have caught it

Add a reconciliation test at `jaffle_shop/tests/assert_daily_revenue_reconciles_to_orders.sql`:

```sql
-- daily_revenue must match orders for every order_date (catches late-arriving data missed by the incremental window)
with expected as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue

from expected

full outer join {{ ref('daily_revenue') }} as actual
    on expected.order_date = actual.order_date

where expected.order_date is null
   or actual.order_date is null
   or expected.orders <> actual.orders
   or abs(expected.revenue - actual.revenue) > 0.005
```

Last night this would have failed on the February dates that received backfilled orders. That would have failed the build instead of letting ops find the gap.

## Separate issue: `region_revenue` is overstated

This is unrelated to the February problem, but I noticed it in the same output. `region_revenue` totals 1838.00, while total payments (the `customers` lifetime value) are 1737.00.

The cause is that `raw_customer_regions` has 106 rows for 100 customers. It looks like a history feed: it has an `updated_at` column, and customer 2 has a 2018-04-02 row. Customers with duplicate rows get their payments counted twice in the join in `models/region_revenue.sql`.

To fix it, dedupe to the latest row per customer before joining (`qualify row_number() over (partition by customer_id order by updated_at desc) = 1`). Then add a `unique` test on `raw_customer_regions.customer_id`, or on a deduped staging model.
