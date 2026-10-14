claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 23 s | USD 0.063 | 2026-10-14 | project bef6b69

## 1. Root cause

The models treat only `status = 'completed'` as completed revenue, but the shop admin also counts `delivered` orders as completed. This is a status-vocabulary mismatch in the data, not a join or fan-out bug.

The numbers reconcile exactly:

| Source | March | Jan to Mar |
|---|---|---|
| `orders` with status `completed` | 143.00 | 424 + 400 + 143 = **967.00** (equals `sum(customers.completed_lifetime_value)`) |
| `delivered` orders (March only) | 136.00 | 136.00 |
| Completed + delivered | **279.00** (admin) | **1103.00** (admin) |

Total revenue matches because `amount` is unaffected. Only the "completed" bucket is wrong.

Files involved:
- `models/customers.sql`: `case when orders.status = 'completed'` in `customer_payments` hard-codes the single status.
- `models/staging/stg_orders.sql`: passes `status` through raw, so `delivered` (which starts appearing in March) is never mapped to completed.
- `models/schema.yml` and `models/staging/schema.yml`: `accepted_values` lists `delivered` as valid, so every test passes while the semantics are wrong. The tests check that a status is known, not how it's classified.

I couldn't see the seed data or the `orders_status` doc block, so "delivered = completed" is inferred from the exact reconciliation above. Confirm that definition with finance before shipping.

## 2. Fix

Define completion once in staging and use it everywhere.

`stg_orders.sql`:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status,
        status in ('completed', 'delivered') as is_completed

    from source

)
```

`orders.sql`: add `orders.is_completed,` to the `final` select, after `orders.status,`.

`customers.sql`:
```sql
sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

Expected result: `sum(completed_lifetime_value)` becomes 1103.00 and March completed orders become 279.00.

Alternatively, if `delivered` is just the new name for `completed` in the source system, map it in `stg_orders` instead (`case when status = 'delivered' then 'completed' else status end as status`). Either way, add the `is_completed` flag or mapping before the next nightly run.

Separate risk, not part of this discrepancy: `raw_customer_regions` has an `updated_at` column, and the seed is documented as "one row per customer". `region_revenue` joins it with no uniqueness test. If a customer has more than one row (a region history), revenue is duplicated. I suggest adding `unique` on `raw_customer_regions.customer_id` or filtering to the latest row.

## 3. Tests that would have caught it

A) Every status must be explicitly classified, so a new status forces a decision. Add this to `schema.yml` for `stg_orders`:
```yaml
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned', 'delivered']
```
Keep it, and pair it with the singular test below, which fails if the flag and the status list drift apart.

B) `tests/assert_completed_revenue_reconciles.sql`: customer-level completed value must equal order-level completed revenue.
```sql
with c as (
    select sum(completed_lifetime_value) as v from {{ ref('customers') }}
),
o as (
    select sum(amount) as v from {{ ref('orders') }} where is_completed
)
select c.v, o.v
from c cross join o
where abs(c.v - o.v) > 0.005
```

C) `tests/assert_terminal_statuses_classified.sql`: a status that is neither classified as completed nor on the explicit non-completed list fails.
```sql
select distinct status
from {{ ref('stg_orders') }}
where not is_completed
  and status not in ('placed', 'shipped', 'return_pending', 'returned')
```

Test B alone wouldn't have fired here, because both sides used the same wrong definition. Test C would have, since it forces the classification of `delivered` to be a deliberate decision. For a true guard, also compare against the admin's monthly completed totals, for example as a seed of finance-signed-off figures.
