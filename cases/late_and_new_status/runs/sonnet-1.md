claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 45 s | USD 0.099 | 2026-10-14 | project f769fdf+wk6ext | prompt c5114118dec9

There are two independent causes. One explains finance's numbers (`delivered` vs `completed`), the other explains ops' numbers (`daily_revenue` is missing late-arriving orders). I haven't run anything; this is from the files, log and output samples you pasted.

## 1. Root cause

**Finance: `delivered` orders are never counted as completed (`models/staging/stg_orders.sql`, `customers.sql`, both `schema.yml` files).**

- `orders` and `customers` agree with each other at 967.00 (424 + 400 + 143). Admin says 1168.00, a gap of 201.00.
- That gap is exactly the `delivered` orders: Feb 65.00 + Mar 136.00 = 201.00. March completed becomes 143 + 136 = 279, which matches admin.
- `delivered` is a status value that now appears in `raw_orders`, and admin treats it as completed. `stg_orders` passes it through untouched, so `status = 'completed'` in `customers.sql` and in any completed-revenue query ignores it.
- The `accepted_values` tests on `stg_orders.status` and `orders.status` were widened to include `delivered`. They pass, but nobody ever decided what `delivered` means for revenue.

**Ops: `daily_revenue` never picks up late-arriving orders (`models/daily_revenue.sql`).**

- Orders 100–105 have February `order_date`s but IDs above the April orders, so they were loaded after `daily_revenue` had already built through 2018-04-09.
- The incremental filter is `order_date >= max(order_date) - 3 days`, which means only the last three days of order dates are rebuilt. Rows backdated to February are never reprocessed.
- The six mismatched dates in your diff query are exactly those six orders: 33 − 27 = 6 orders and 480 − 415 = 65.00. They are also the six `delivered` February orders, so the two bugs overlap in February.
- The code comment says payments land a day or two after the order. The window is keyed on `order_date`, not on load time, so it doesn't protect against that.
- Uniqueness and not-null tests can't detect missing rows.

## 2. Fix

**Status.** First confirm with the source owner that `delivered` means completed. The 201.00 arithmetic strongly suggests it. Then normalise it in `stg_orders.sql`:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        case status
            when 'delivered' then 'completed'
            else status
        end as status

    from source
```

Remove `delivered` from the `accepted_values` lists in `models/schema.yml` and `models/staging/schema.yml`. Add the real guard against new raw statuses on the seed:

```yaml
seeds:
  - name: raw_orders
    columns:
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'delivered', 'return_pending', 'returned']
```

A new raw status then fails here, where someone has to decide how to map it.

**daily_revenue.** The data is small, so the simplest fix is to make it a plain table:

```sql
{{ config(materialized='table') }}
```

If it has to stay incremental, the window must be based on ingestion time, not business date. That needs a `loaded_at` column from the ingestion job, with the filter on `loaded_at` and `unique_key='order_date'` re-aggregating all rows for the affected dates. Without that column, a periodic full refresh is the only safe option.

Either way, rebuild once now, because the stale rows are already in the table:

```
dbt run --select daily_revenue --full-refresh
dbt build
```

**Expected after the fix:**

- Completed revenue since January is 1168.00.
- March completed is 279.00.
- `sum(customers.completed_lifetime_value)` is 1168.00.
- February in `daily_revenue` is 33 orders and 480.00.

## 3. Tests

**Reconcile `daily_revenue` against `orders`.** This would have caught the late-arriving rows. Put it in `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with o as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

),

d as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as expected_orders,
    d.orders as actual_orders,
    o.revenue as expected_revenue,
    d.revenue as actual_revenue

from o
full outer join d
    on o.order_date = d.order_date

where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

**Check the completed totals tie out.** This catches status-mapping drift between `customers` and `orders`. Put it in `tests/assert_completed_revenue_ties_out.sql`:

```sql
select *
from (

    select
        (select round(sum(amount), 2) from {{ ref('orders') }} where status = 'completed') as orders_completed,
        (select round(sum(completed_lifetime_value), 2) from {{ ref('customers') }}) as customers_completed

)
where orders_completed is distinct from customers_completed
```

The `raw_orders.status` `accepted_values` test above is what would have caught `delivered` appearing. It would have forced an explicit decision instead of a widened allow-list.

**Side note:** `raw_customer_regions` has an `updated_at` column, and its only tests are `not_null`. If it holds more than one row per customer, `region_revenue` will double-count. That model isn't part of this complaint. A `unique` test on `raw_customer_regions.customer_id` would settle it.
