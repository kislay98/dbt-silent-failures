claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 19 s | USD 0.171 | 2026-10-15 | project ad0b16b | prompt 6cd8da3825cf

## 1. Root cause

**The `status` values in `raw_orders` (seed `seeds/raw_orders.csv`) have inconsistent casing.** Some rows say `completed` and others say `Completed`. The same split appears for `Placed`, `Shipped` and `Return_pending`. `stg_orders.sql` passes `status` through unchanged, and `customers.sql` filters case-sensitively:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

So only the lowercase rows are counted. The numbers match exactly:

| status | orders | amount |
|---|---|---|
| `completed` | 36 | 604.00 ← what `customers` reports |
| `Completed` | 31 | 499.00 ← silently dropped |
| **total** | 67 | **1103.00** ← finance ledger |

`sum(orders.amount)` = 1672 is still correct because it doesn't filter on status.

**Why the tests passed:** the `accepted_values` tests on `status` in both `models/staging/schema.yml` and `models/schema.yml` were widened to also allow `'Placed', 'Shipped', 'Completed', 'Return_pending', 'Returned'`. That turned the one test that should have flagged the bad feed into one that approves it. Nothing in the project checks that revenue reconciles.

## 2. Fix

Normalize the value once, at the staging layer, so every downstream model sees a single canonical form.

`models/staging/stg_orders.sql`:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        lower(trim(status)) as status

    from source

)
```

Then tighten the tests back to the canonical values only, in both `models/staging/schema.yml` and `models/schema.yml`:
```yaml
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

After `dbt build`, `sum(customers.completed_lifetime_value)` should be 1103.00. You should also raise the casing change with whoever produces the orders feed. The staging fix protects you from it, but the feed changed format without notice.

## 3. Tests that would have caught it

**a) Strict `accepted_values` (above).** With the original lowercase-only list, the first `Completed` row would have failed `accepted_values_stg_orders_status` and blocked the build. The key point is to not widen this list to make a failing test pass. Fix the data in staging instead.

**b) A revenue reconciliation test**, which is independent of how the status values are spelled. Add `tests/assert_completed_revenue_reconciles.sql`:

```sql
-- Completed lifetime value on customers must equal completed order revenue on orders,
-- matched case-insensitively so a casing drift in the feed can't hide rows from either side
with customer_side as (

    select coalesce(sum(completed_lifetime_value), 0) as amount
    from {{ ref('customers') }}

),

order_side as (

    select coalesce(sum(amount), 0) as amount
    from {{ ref('orders') }}
    where lower(status) = 'completed'

)

select
    customer_side.amount as customer_completed,
    order_side.amount as order_completed
from customer_side, order_side
where abs(customer_side.amount - order_side.amount) > 0.01
```

Against this week's data it returns 604 vs 1103 and fails. After the fix it returns no rows.

A smaller unrelated issue: `models/schema.yml` documents a `customers.total_order_amount` column that doesn't exist. The model outputs `customer_lifetime_value`, so that doc entry should be renamed.
