claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 77 s | USD 0.012 | 2026-10-14 | project ad0b16b | prompt eb0b3a80ab67

## 1. Root cause

**The 136.00 completed-revenue gap isn't reproducible inside the models, so I can't name one file as its cause from this evidence.** Here is what the artifacts do establish.

- **The gap is exactly 136.00 and sits entirely in March.** Admin shows 279.00 vs 143.00 for March, and 1103.00 vs 967.00 since January. The all-time difference equals the March difference, so the discrepancy is in March and nowhere else.
- **The warehouse agrees with itself.** `orders` has 59 completed orders totalling 967.00, and `customers.completed_lifetime_value` sums to 967.00. The `status` aggregate matches both. No model drops or double-counts completed revenue relative to `raw_orders` and `raw_payments`.
- **A timezone shift can't explain it.** Moving orders across the UTC/AEST boundary changes the monthly split but not the all-time total, which still differs by 136.00.
- **The most likely cause is upstream of dbt.** Either the ingested `raw_orders` or `raw_payments` tables differ from the admin's source (orders missing, statuses not re-ingested, or payments whose `order_id` doesn't match any row in `raw_orders`), or the admin is counting something other than order status. Orphan payments would be silently dropped by the left join from `orders` in `orders.sql` and by the inner join in `region_revenue.sql`, so no test would notice.

**A defect I can prove in the files: `daily_revenue.sql`.** Its March and April revenue doesn't match `orders`, while order counts do:

| Month | `orders` count | `daily_revenue` orders | `orders` amount | `daily_revenue` revenue |
|---|---|---|---|---|
| 2018-03 | 35 | 35 | 595.27 | 622.00 |
| 2018-04 | 8 | 8 | 106.33 | 139.00 |

January and February match. The incremental filter only rebuilds `order_date >= max(order_date) - 3 days`, so older rows freeze at the payment amounts they had when first built. Later payment corrections never reach them, and the comment's assumption that payments land within two days doesn't cover corrections. This model doesn't filter on status, so it doesn't explain the 136.00, but finance may read revenue from it.

**Also worth verifying:** the build log has no seed step, so the `raw_*` tables come from the ingestion job, not from the seed CSVs. `ref('raw_orders')` resolves by name to whatever table exists. If the CSVs and the ingested tables differ, the project files don't describe the data that is actually being built on.

**Confidence:** high that `daily_revenue` is stale. Low on the 136.00 cause, which needs the checks below.

## 2. Fix

**`models/daily_revenue.sql`:** remove the incremental behaviour and rebuild the full table. It's 69 rows, so a full rebuild is cheap, and it removes the frozen-history problem.

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

Update the `daily_revenue` description in `schema.yml`, which currently says it's incremental and rebuilds three days.

**For the 136.00 gap, confirm the cause before changing code.** Run these against the warehouse:

```sql
-- payments with no matching order (silently dropped everywhere)
select count(*), round(sum(p.amount) / 100, 2)
from raw_payments p
anti join raw_orders o on p.order_id = o.id;

-- March completed orders as ingested vs what the admin shows
select o.id, o.ordered_at, o.status, sum(p.amount) / 100 as amount
from raw_orders o
left join raw_payments p on p.order_id = o.id
where o.status = 'completed' and o.ordered_at >= '2018-03-01' and o.ordered_at < '2018-04-01'
group by 1, 2, 3
order by 2;
```

Diff the second result against an export of the admin's March completed orders. Orders missing from `raw_orders`, or present with a different status, point to the ingestion job. Orphan payments point to a missing or late `raw_orders` load.

## 3. Tests that would have caught this

Add these to `tests/` (singular tests fail when they return rows):

- **Revenue reconciliation (catches the `daily_revenue` drift):**

```sql
-- tests/assert_daily_revenue_matches_orders.sql
select d.order_date, d.revenue, o.revenue as orders_revenue
from {{ ref('daily_revenue') }} d
join (
    select order_date, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
) o using (order_date)
where abs(d.revenue - o.revenue) > 0.005
```

- **Orphan payments (catches the upstream gap):** a `relationships` test on `stg_payments.order_id` pointing to `stg_orders.order_id`. Set it to `error` once the orphan query above returns zero rows.

- **Completed-revenue reconciliation between models:** assert that `sum(customers.completed_lifetime_value)` equals `sum(orders.amount)` where `status = 'completed'`. This passes today, so it guards against future regressions rather than this bug.

- **Source totals (the test that would have caught the admin mismatch):** compare `raw_orders` and `raw_payments` row counts and totals per month against a control total from the ingestion job or the admin export. This needs an external source, so it belongs in the ingestion pipeline or a `dbt_utils`-style check fed by the export.

- **Key uniqueness on the seeds:** `raw_payment_methods.payment_method` and `raw_customer_regions.customer_id` only have `not_null`. A duplicate payment method would fan out `orders.sql` and inflate every amount. `raw_customer_regions` has an `updated_at` column, which suggests history rows could appear. Add `unique` to both.

Also, `schema.yml` documents a `total_order_amount` column on `customers` that doesn't exist. The model outputs `customer_lifetime_value`. Fix the description so finance isn't looking for the wrong field.
