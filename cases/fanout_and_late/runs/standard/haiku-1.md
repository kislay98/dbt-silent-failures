claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 35 s | USD 0.007 | 2026-10-14 | project ad0b16b | prompt 6c2f9fd42779

## 1. Root cause

**`jaffle_shop/models/daily_revenue.sql`**: the incremental filter can't reach February.

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
{% endif %}
```

The filter is relative to `max(order_date)` already in the `daily_revenue` table, which is around early April 2018. Each run rebuilds only roughly the last three days and leaves every earlier date untouched. The six backfilled February orders (65.00) are dated in February, so no run ever rebuilds that day. `daily_revenue` keeps its old February values (27 orders, 415.00).

`orders` is a plain table rebuilt from `stg_orders` on every run, so it picks up the backfill (33 orders, 480.00). The two tables disagree only for February, which fits the backfill being the only change. The 65.00 gap is exactly the six backfilled orders.

The `-- payments can land a day or two after the order` comment is also wrong about what the window is for. Revenue comes from `orders.amount`, which is a full rebuild, so the window doesn't fix late payments either. It only creates the false impression that the model is self-healing.

Every test passed because no test compares `daily_revenue` with `orders`. The `unique` and `not_null` checks on `daily_revenue` can't detect stale rows.

**A second, separate bug in `jaffle_shop/models/region_revenue.sql`**: `raw_customer_regions` has 106 rows for 100 customers, and it has an `updated_at` column. That means some customers have more than one region row. The join `orders.customer_id = regions.customer_id` fans out the payments. Evidence: the `region_revenue` revenue column sums to 1838.0, but total order amount is 1737.0. The extra 101.0 is duplicated revenue. The `unique` test on `region` passes only because the model groups by region. `raw_customer_regions.customer_id` has no `unique` test in `models/schema.yml`, which is why this wasn't caught.

I diagnosed this from the files and the query output. I did not run the project.

## 2. Fix

**a) Make `daily_revenue` a full rebuild.** It is a 105-row aggregate, so incremental buys nothing, and the table is already the project default. Replace the config and drop the filter:

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

Update the description in `models/schema.yml` to match, for example "Orders and revenue per order date. Rebuilt in full on each run." Then run `dbt run --select daily_revenue`. A plain run is enough once it's a table. If you keep it incremental, you need `--full-refresh` once to fix February, and any future backfill will drift again.

**b) Dedupe the region feed** in `jaffle_shop/models/region_revenue.sql`, assuming `updated_at` marks the current version of each customer's region (please confirm with the feed owner):

```sql
regions as (

    {#-
    The regions feed is loaded as a seed, like the other raw tables
    #}
    select customer_id, region
    from {{ ref('raw_customer_regions') }}
    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

),
```

After this, `region_revenue` should sum to 1737.0.

## 3. Tests that would have caught it

**a) A reconciliation test** that fails the nightly build when `daily_revenue` disagrees with `orders`. Save as `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`:

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

Against the current data this returns the February rows and fails, which is the point.

**b) A uniqueness test on the region feed**, so the fan-out fails the build. In `jaffle_shop/models/schema.yml`, under `seeds:` → `raw_customer_regions`:

```yaml
      - name: customer_id
        tests:
          - unique
          - not_null
```

This test fails on the current data (106 rows, 100 customers) and would have flagged the `region_revenue` problem before it reached the dashboard.
