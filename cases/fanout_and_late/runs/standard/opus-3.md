claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 26 s | USD 0.175 | 2026-10-14 | project ad0b16b | prompt 6c2f9fd42779

# daily_revenue's incremental window can't see the February backfill

## 1. Root cause

The data is correct. The bug is the incremental filter in `jaffle_shop/models/daily_revenue.sql`:

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
{% endif %}
```

On each run, the model only rebuilds order dates within 3 days of the newest date already in the table, which is early April 2018. It assumes data never arrives for older dates. The store backfill broke that assumption: last night's load added six orders with **February** `order_date`s (e.g. order 103 on 2018-02-17 and order 105 on 2018-02-26). February was outside the rebuild window, so the run never recomputed those days.

- `orders` is a full `table` rebuild, so it picked up the backfill: 33 orders, 480.00.
- `daily_revenue` kept its old February rows: 27 orders, 415.00.
- The gap is exactly the backfill: 33 − 27 = 6 orders and 480 − 415 = 65.00.

Every test passed because each test checks one table on its own. Nothing compares `daily_revenue` against `orders`.

## 2. Fix

**Repair the table now:**

```bash
dbt build --full-refresh -s daily_revenue
```

**Prevent a repeat:** `daily_revenue` has 69 rows and `orders` has 105, so incremental gives no real speedup here. Also, `raw_orders` has no `loaded_at` column, so the model cannot tell which past dates received late data. I recommend making it a plain table:

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

Update the `schema.yml` description to match: remove "Incremental, rebuilds the last three days on each run".

If it ever needs to be incremental again, the ingestion job should add a load timestamp to `raw_orders` and `raw_payments`. The model would then rebuild every `order_date` touched by rows loaded since the last run, not a fixed 3-day window.

## 3. Test that would have caught it

`tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must agree with orders for every order date
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
    or expected.orders != actual.orders
    or abs(coalesce(expected.revenue, 0) - coalesce(actual.revenue, 0)) > 0.005
```

Run against last night's build, this returns the February dates of the six backfilled orders, and `dbt build` fails.

## Aside: a separate bug in `region_revenue`

`raw_customer_regions` has 106 rows for 100 customers. Customer 2, for example, has a row with `updated_at` 2018-04-02. `region_revenue` joins on `customer_id` without deduplicating, so those customers' payments are counted more than once. Region revenue totals 1838.00, while `customers.customer_lifetime_value` totals 1737.00.

To fix it, keep only the latest row per customer:

```sql
qualify row_number() over (partition by customer_id order by updated_at desc) = 1
```

Then add a `unique` test on `raw_customer_regions.customer_id`, or on a staging model built from it.
