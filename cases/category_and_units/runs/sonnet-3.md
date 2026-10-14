claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 38 s | USD 0.090 | 2026-10-14 | project 8ee5734 | prompt d0c0d63b3bb2

## 1. Root cause

I can't see the seed data, so this is inferred from the numbers.

**The `delivered` status is the problem.** `completed_lifetime_value` and `status = 'completed'` filters in `models/customers.sql` and `models/orders.sql` are doing what they say. The data has changed underneath them.

- Admin minus `orders` for March is 279.00 − 143.00 = **136.00**. Admin minus `orders` since January is 1103.00 − 967.00 = **136.00**, so the whole gap is in March.
- The March `delivered` orders (66, 67, 68, 69, 70, 73, 75, 76) sum to 134.02 in `orders`. Order 76 shows 0.02. If it is really 2.00, the total is exactly **136.00**.
- `delivered` appears only in March. It looks like the source system renamed `completed` to `delivered` from about 2018-03-10, and nothing in the project maps it back.

**Why every test passed.** `delivered` was added to `accepted_values` in both `models/schema.yml` and `models/staging/schema.yml`. That list is the only guard on status, so the new value went through silently and fell out of every `status = 'completed'` filter. This affects `orders`, `customers.completed_lifetime_value` (967.00), and anything else that filters on completed.

**Second data problem (likely).** Orders 76, 81, 86, 92, 94 and 95 have tiny coupon amounts (0.02, 0.02, 0.23, 0.02, 0.07, 0.24), and all are on or after 03-20. They look like coupon rows loaded in dollars while `models/staging/stg_payments.sql` divides everything by 100, so a $2 coupon becomes 0.02. The 136.00 reconciliation above supports this for order 76. Check the raw coupon rows in `raw_payments` to confirm.

## 2. Fix

Confirm with the source team that `delivered` means completed. Then normalise it in `models/staging/stg_orders.sql`:

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

- Remove `delivered` from the `accepted_values` list in `models/schema.yml`, so the marts only accept canonical statuses.
- Keep `delivered` in the staging list only if you want to allow the raw value, and add a comment explaining why.
- After the rebuild, March completed should be 279.00 and completed since January 1103.00 (the latter once the coupon amounts are fixed).
- For the coupon units, fix the ingestion job so coupon amounts are loaded in cents. Patching `stg_payments.sql` with a coupon-specific `* 100` is only safe if the feed is consistently in dollars from a known date.
- `daily_revenue` is incremental and only reprocesses the last 3 days, so run `dbt run --full-refresh -s daily_revenue` after the fix. Otherwise the old values stay.

## 3. Tests

**Singular test that fails on any non-canonical status in the mart** (`tests/assert_orders_status_canonical.sql`):

```sql
select order_id, status
from {{ ref('orders') }}
where status not in ('placed', 'shipped', 'completed', 'return_pending', 'returned')
```

**Reconciliation against the shop admin.** Put the admin's monthly completed totals in a seed and compare (`tests/assert_completed_matches_admin.sql`):

```sql
select o.month, o.amount, a.amount as admin_amount
from (
    select date_trunc('month', order_date) as month, sum(amount) as amount
    from {{ ref('orders') }}
    where status = 'completed'
    group by 1
) o
join {{ ref('admin_completed_by_month') }} a using (month)
where abs(o.amount - a.amount) > 0.01
```

**Unit-sanity check for the coupon problem** (`tests/assert_no_subdollar_payments.sql`, set to `severity: warn` if some legitimate small payments exist):

```sql
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount > 0 and amount < 1
```

**Latent issue to check.** `raw_customer_regions` has an `updated_at` column and no uniqueness test on `customer_id`. If a customer has several rows, `region_revenue` fans out and double-counts. Add `unique` on `customer_id` for that seed or dedupe to the latest row.
