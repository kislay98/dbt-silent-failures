claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 28 s | USD 0.094 | 2026-10-14 | project ad0b16b | prompt 2b2290cfef14

I can't query the warehouse from here, so this comes from the numbers in your output. There are two separate problems.

## 1. Root causes

**A. Completed revenue: `delivered` orders are being left out (`models/staging/stg_orders.sql`, `customers.sql`, and `schema.yml`).**
- The admin's 1168.00 minus our 967.00 is exactly 201.00, which is the total of the `delivered` row in the status breakdown.
- The March gap is 279.00 − 143.00 = 136.00. I can't confirm that all 136.00 is `delivered`, but the sampled `delivered` order 75 is dated 2018-03-18. That fits `delivered` being a newer status the source started emitting for orders that used to arrive as `completed`.
- `stg_orders` passes the status through untouched, and `customers.completed_lifetime_value` and the finance filter test `status = 'completed'` literally. The `accepted_values` tests list `delivered` as valid, so nothing flags it, and the unclassified status silently drops out of "completed".
- Total revenue still matches the admin because it doesn't filter on status.

**B. February in `daily_revenue` is missing late-arriving orders (`models/daily_revenue.sql`).**
- The gap is 6 orders and 65.00 (33 → 27 orders, 480 → 415). Only February differs.
- The incremental filter only rebuilds `order_date >= max(order_date) - 3 days`. Any February order or payment that lands after that day's run is never picked up again.
- The ingestion job evidently backfilled February after `daily_revenue` had moved on. `orders` is a full table so it shows the right numbers, while `daily_revenue` froze the old ones.
- The incremental window is also in order-date terms, so it can't catch backfills. The model is tiny (69 rows), so incremental isn't buying anything.
- The test suite only checks `unique` and `not_null` on `daily_revenue`, so a stale but well-formed table passes.

## 2. Fix

First confirm with finance that `delivered` counts as completed. Then normalize it in `stg_orders.sql`:

```sql
renamed as (
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        case when status = 'delivered' then 'completed' else status end as status
    from source
)
```

For `daily_revenue.sql`, drop the incremental config so it always reflects `orders`:

```sql
{{ config(materialized='table') }}

select order_date, count(*) as orders, sum(amount) as revenue
from {{ ref('orders') }}
group by order_date
```

Then run `dbt build --full-refresh`. A full refresh is needed once either way, because the stored February rows are stale. If the model must stay incremental, key the lookback on a load timestamp rather than `order_date`.

Clean up `schema.yml` too: remove `delivered` from the `accepted_values` lists after the mapping. The `customers` description for `total_order_amount` is also stale, since the column is `customer_lifetime_value`.

## 3. Tests

**Reconciliation, which would have caught B.** `tests/assert_daily_revenue_matches_orders.sql`:

```sql
select o.order_date, o.orders, d.orders as daily_orders, o.revenue, d.revenue as daily_revenue
from (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
) o
full join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

**Explicit status classification, which would have caught A.** Add a `raw_order_statuses` seed with columns `status` and `is_completed`. Then add a `relationships` test from `stg_orders.status` to it, plus `unique` and `not_null` on the seed. A new status like `delivered` then fails the build until someone decides whether it counts as completed.

**Finance tie-out.** A test that compares `sum(amount) where status = 'completed'` per month against a finance-supplied control seed would also have caught A.
