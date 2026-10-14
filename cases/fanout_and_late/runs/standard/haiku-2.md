claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 30 s | USD 0.006 | 2026-10-14 | project ad0b16b | prompt 6c2f9fd42779

## 1. Root cause

**`jaffle_shop/models/daily_revenue.sql`** is the problem. It's an incremental model that rebuilds only the last three days:

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
{% endif %}
```

The window is anchored to `max(order_date)` in the target table, so it only covers recent dates. The six February orders arrived in last night's load with `order_date` in February. That date falls outside the window, so `delete+insert` never touches February and the existing February row (27 orders, 415.00) stays stale.

`orders` is a full-refresh `table` model, so it picks up the backfill (33 orders, 480.00). That's why the two disagree.

The 65.00 figure matches the backfill exactly, which confirms the diagnosis. The 3-day rule was written for payments landing late, not for order rows arriving months late.

**Why the tests didn't catch it:** every test checks one table in isolation (`unique`, `not_null`, `accepted_values`). Nothing compares `daily_revenue` to `orders`. The `unique` test on `order_date` passes because the stale row is still unique.

## 2. Fix

**Immediate correction** (rebuilds the table from the current `orders`):

```sh
dbt run --select daily_revenue --full-refresh
```

**Permanent fix:** remove the incremental logic. `orders` is already rebuilt in full, and aggregating roughly 100 rows is trivial, so the incremental optimization adds only drift risk.

`jaffle_shop/models/daily_revenue.sql`:

```sql
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

Add the materialization to the top of the file, or set it in `dbt_project.yml`. Either way, `materialized='table'` is required; without it the project default would apply.

Also update the description in `jaffle_shop/models/schema.yml`. It currently says "Incremental, rebuilds the last three days on each run", which will no longer be true.

If volume later makes this slow, make `orders` incremental first, keyed on a load or updated timestamp that captures backfills, not on `order_date`.

## 3. Test that would have caught it

Add a singular test that compares the two tables on every date. It fails the build if they diverge. Save it as `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`:

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
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue

from expected
full outer join actual
    on expected.order_date = actual.order_date

where expected.orders is distinct from actual.orders
   or round(expected.revenue, 2) is distinct from round(actual.revenue, 2)
```

Singular tests pass when they return zero rows. This one returns every date that disagrees, including dates present in only one table. Against the current data it would have returned 2018-02 dates with 6 missing orders, and it would fail the build the night the backfill landed.

## Other issue I noticed (not part of the February mismatch)

**`region_revenue` is inflated by duplicate customers.** `raw_customer_regions` has 106 rows for 100 customers, and it has an `updated_at` column, which suggests multiple snapshots per customer. `region_revenue` joins on `customer_id` without deduplicating, so each duplicate multiplies that customer's payments. Region revenue sums to 1838.00, but total order amount is 1737.00. The schema only tests `not_null` on `customer_id`, so this slipped through. I'd fix it by keeping the latest row per customer (`row_number() over (partition by customer_id order by updated_at desc)`) and adding a `unique` test on `customer_id` in the seed's schema. I haven't changed anything; tell me if you want that applied.
