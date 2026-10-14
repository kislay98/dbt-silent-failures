claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 77 s | USD 0.011 | 2026-10-14 | project ad0b16b | prompt eb0b3a80ab67

I can't identify the exact 136.00 from these artifacts. The dbt models reproduce the warehouse faithfully, so the gap is probably in the raw layer or in what the admin counts. I did find a real defect in `daily_revenue`, but it doesn't explain the completed-revenue gap.

## 1. Root cause

**What the artifacts establish:**

- The gap is exactly 136.00 and it all falls in March. The admin and the models differ by 1103.00 − 967.00 = 136.00 since January, and by 279.00 − 143.00 = 136.00 in March. April and February contribute nothing.
- No dbt model drops completed revenue. `orders` left-joins from `stg_orders` (99 rows in, 99 out). `customers` and `region_revenue` use joins that also preserve every order. The three totals agree: `orders.amount` sums to 1612.60, `customers.customer_lifetime_value` sums to 1612.60, and `region_revenue.revenue` sums to 1612.60. Nothing is duplicated or lost inside dbt.
- `stg_payments` divides `amount` by 100 and has no null or unit anomalies in the sample.
- Payments whose `order_id` isn't in `raw_orders` are silently dropped by every model. `stg_payments` has no relationship test to `stg_orders`, so nothing flags them.

**So the 136.00 is in the raw data or the admin's data, not in the SQL.** The likely causes are:

- Orders the admin has in March that are missing from `raw_orders`, from an ingestion gap.
- Status drift in `raw_orders.status`, with the admin showing a more recent status than the snapshot.
- Payments whose `order_id` doesn't match any row in `raw_orders` (orphans).

To settle it, get the admin's list of March completed orders (`order_id`, `amount`) and run:

```sql
-- Admin orders missing from the warehouse, or with a different status
select a.order_id, a.amount, o.status
from admin_march_completed a
left join orders o on o.order_id = a.order_id
where o.order_id is null or o.status <> 'completed';

-- Orphan payments that every model silently drops
select p.order_id, sum(p.amount) / 100.0 as amount
from raw_payments p
where not exists (select 1 from raw_orders o where o.id = p.order_id)
group by 1;
```

**The real defect: `daily_revenue` is stale.** Its incremental filter rebuilds only the last three days relative to the table's own `max(order_date)`. Older dates are never refreshed when payments change. The orders table is rebuilt every run, but `daily_revenue` isn't, so the two disagree:

| Month | `orders.amount` | `daily_revenue.revenue` | Difference |
|---|---|---|---|
| Jan | 496.00 | 496.00 | 0 |
| Feb | 415.00 | 415.00 | 0 |
| Mar | 595.27 | 622.00 | 26.73 |
| Apr | 106.33 | 139.00 | 32.67 |

Total drift is 59.40. The file is `jaffle_shop/models/daily_revenue.sql`. Its comment assumes late payments only affect the last three days, which doesn't hold for this design.

`daily_revenue` has no status column, so this doesn't produce the 136.00 completed-revenue gap. It is still wrong and should be fixed.

## 2. Fix

**For `daily_revenue` (definite):** it's 69 rows, so rebuilding it as a table costs nothing. Replace the incremental config and filter in `jaffle_shop/models/daily_revenue.sql`:

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

**For the 136.00 (pending the queries above):**

- If rows are missing from `raw_orders` or `raw_payments`, the fix belongs in the ingestion job, not dbt. Re-run the load for the affected March orders.
- If statuses have drifted, the ingestion job needs to refresh `raw_orders.status`, or the admin's status history needs to be reconciled.
- If orphan payments exist, decide whether they are real revenue. If they are, the orders feed is incomplete; if not, they should be excluded explicitly with a documented filter.

## 3. Tests that would have caught this

The current tests check only column shape (not_null, unique, accepted_values). None of them compare values across models or against the source, so drift and dropped rows pass silently.

**a) Orphan payments (catches the silent drop).** Add to `jaffle_shop/models/staging/schema.yml` under `stg_payments.order_id`:

```yaml
      - name: order_id
        tests:
          - relationships:
              arguments:
                to: ref('stg_orders')
                field: order_id
```

**b) `daily_revenue` must match `orders` (catches the stale incremental).** Create `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- Returns a row (and fails) for any date where the two tables disagree
select
    coalesce(d.order_date, o.order_date) as order_date,
    d.revenue as daily_revenue,
    o.revenue as orders_revenue
from {{ ref('daily_revenue') }} d
full outer join (
    select order_date, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
) o on d.order_date = o.order_date
where d.revenue is null
   or o.revenue is null
   or abs(d.revenue - o.revenue) > 0.005
```

**c) Raw-to-staging totals (catches rows dropped between the raw layer and staging).** Create `jaffle_shop/tests/assert_payments_total_matches_raw.sql`:

```sql
-- Fails if staging loses or invents payment value relative to the raw table
select
    (select sum(amount) / 100.0 from {{ ref('raw_payments') }}) as raw_total,
    (select sum(amount) from {{ ref('stg_payments') }}) as stg_total
where abs(raw_total - stg_total) > 0.005
```

Test (b) would have failed on the first run after the March rows were last changed, and test (a) would have flagged any orphan payments. Test (c) guards the staging layer. None of these can detect the 136.00 by itself. Only a comparison against the admin's own completed-revenue figure can do that, so it's worth adding a reconciliation for completed revenue by month against the admin's export, run as part of the nightly build.
