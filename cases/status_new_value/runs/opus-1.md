claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 30 s | USD 0.127 | 2026-10-14 | project f769fdf | prompt 962ccbf68360

# Diagnosis: `delivered` orders are not counted as completed

## 1. Root cause

The source system started writing a new order status, **`delivered`**, in March 2018. The shop admin counts `delivered` orders as completed. The dbt project does not.

The numbers match this exactly:

| | dbt (`status = 'completed'`) | + `delivered` | Admin |
|---|---|---|---|
| March | 143.00 | + 136.00 | **279.00** |
| Since January | 967.00 (424 + 400 + 143) | + 136.00 | **1103.00** |

Total revenue still matches because the amounts are correct. Only the status classification is wrong. All 1672.00 is accounted for in `orders`.

The files involved:

- **The data, `raw_orders.status`:** it now contains `delivered`. There are 8 such orders, all in March. There are no `completed` orders after March, so `delivered` looks like a replacement value rather than an extra step in the order lifecycle.
- **`models/staging/stg_orders.sql`:** it passes `status` through unchanged.
- **`models/customers.sql`:** `completed_amount` hard-codes `orders.status = 'completed'`. Anyone filtering `orders` on `status = 'completed'` has the same blind spot.
- **`models/schema.yml` and `models/staging/schema.yml`:** both `accepted_values` tests had `'delivered'` added to their lists. That is why the build is green. The test was widened to accept the new value, but no model was taught what the value means.

I inferred "delivered = completed" from the arithmetic. Please confirm it with whoever owns the shop platform before shipping the fix.

## 2. Fix

Normalise the status once, in staging. That way every downstream model (`orders`, `customers`, and anything added later) uses the same definition. The raw value is kept for traceability.

`models/staging/stg_orders.sql`:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- the shop platform reports completed orders as 'delivered' from March 2018
        case when status = 'delivered' then 'completed' else status end as status,
        status as source_status

    from source

)
```

In both `schema.yml` files, give `status` its original list, without `delivered`. Then add a test for the raw value in staging. Any future new status will fail the build and force someone to map it.

```yaml
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
      - name: source_status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned', 'delivered']
```

Also update the `orders_status` doc block to describe the mapping.

After `dbt build`, the results should be:
- March completed in `orders`: 279.00
- `sum(customers.completed_lifetime_value)`: 1103.00

## 3. A test that would have caught this

The original `accepted_values` test would have caught it, if it had failed on `delivered` instead of being edited to pass. The rule to adopt: only add a value to `accepted_values` once every model that branches on that column handles it.

As a second guard, add a reconciliation test against the admin's own definition of completed. For example, `tests/assert_completed_revenue_reconciles.sql`:

```sql
-- completed revenue in the marts must equal payments on orders the source considers completed
with source_completed as (
    select sum(p.amount) as amount
    from {{ ref('stg_payments') }} p
    join {{ ref('stg_orders') }} o using (order_id)
    where o.source_status in ('completed', 'delivered')
),
mart_completed as (
    select sum(completed_lifetime_value) as amount from {{ ref('customers') }}
)
select *
from source_completed, mart_completed
where abs(source_completed.amount - mart_completed.amount) > 0.01
```

This test returns no rows when the totals reconcile. A row means the marts and the source disagree, so the test fails.

## Unrelated risk spotted while reviewing

`raw_customer_regions` has an `updated_at` column, which suggests it can hold several rows per customer. Its seed tests check `not_null` but not `unique` on `customer_id`. If a customer has more than one row, the join in `region_revenue.sql` will double-count revenue. Consider adding a `unique` test on `customer_id`, or deduplicating to the latest row per customer.
