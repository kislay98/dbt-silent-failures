claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 18 s | USD 0.169 | 2026-10-15 | project ad0b16b | prompt 6cd8da3825cf

## 1. Root cause

The `status` values in the raw orders feed (`seeds/raw_orders.csv`, loaded as `raw_orders`) arrive in **mixed case**. Some rows say `completed` and some say `Completed`. The same is true for `Placed`, `Shipped` and `Return_pending`. Nothing downstream handles this:

- **`models/staging/stg_orders.sql`** passes `status` through unchanged.
- **`models/customers.sql`** filters case-sensitively with `orders.status = 'completed'`, so every `Completed` order is counted as 0.

The numbers reconcile exactly:

| status | orders | amount |
|---|---|---|
| `completed` | 36 | 604.00 ← what `customers` reports |
| `Completed` | 31 | 499.00 ← silently dropped |
| **total** | 67 | **1103.00** ← finance ledger |

The tests didn't catch it because someone widened the `accepted_values` lists to include the title-case variants. This happened in both **`models/staging/schema.yml`** (`stg_orders.status`) and **`models/schema.yml`** (`orders.status`). The tests now accept the bad casing instead of rejecting it. `sum(orders.amount)` is unaffected (1672.00) because it doesn't filter on status.

## 2. Fix

Normalize the status once, in staging, so every downstream model sees one canonical value.

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

No change is needed in `customers.sql`. After a rebuild, `sum(completed_lifetime_value)` should be 1103.00. This also fixes the other statuses (`Placed`/`Shipped`/`Return_pending`), which would break any future filter on those values. Separately, ask the owner of the orders feed why the casing changed. The data shows the title-case values appearing on newer orders, so the upstream format likely changed partway through the period.

## 3. Tests that would have caught it

**a) Revert `accepted_values` to the canonical lowercase set** in both `models/staging/schema.yml` and `models/schema.yml`:
```yaml
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```
With the lowercase-only list, the `stg_orders` test would have failed on this week's data. With the fix in place, it passes and will guard against new variants. If you'd rather catch raw-feed drift at the source too, add the same test on the `raw_orders` seed's `status` column.

**b) A reconciliation test**, which catches any mismatch between the per-order and per-customer revenue, whatever the cause. Add `tests/assert_completed_ltv_reconciles_to_orders.sql`:
```sql
-- completed revenue rolled up to customers must equal completed revenue on orders
with by_orders as (

    select coalesce(sum(amount), 0) as completed_amount
    from {{ ref('orders') }}
    where lower(status) = 'completed'

),

by_customers as (

    select coalesce(sum(completed_lifetime_value), 0) as completed_amount
    from {{ ref('customers') }}

)

select *
from by_orders
cross join by_customers
where abs(by_orders.completed_amount - by_customers.completed_amount) > 0.01
```
On this week's build, this test returns one row (1103.00 vs 604.00) and fails.
