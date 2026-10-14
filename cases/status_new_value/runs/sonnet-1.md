claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 19 s | USD 0.057 | 2026-10-14 | project bef6b69

## 1. Root cause

The numbers reconcile exactly once `delivered` is counted as completed. The 'delivered' orders are 8 orders worth 136.00 in March.

| | `completed` only (our models) | + `delivered` (admin) |
|---|---|---|
| March | 143.00 | 143 + 136 = **279.00** |
| Jan-Mar | 424 + 400 + 143 = 967.00 (matches `customers.completed_lifetime_value`) | 967 + 136 = **1103.00** |

Total revenue matches in every month because only the status classification differs. The payments and amounts are fine.

- **`models/customers.sql`**: `sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount` hardcodes a single status.
- **`raw_orders` (via `stg_orders.sql`)**: the shop started emitting a new terminal status, `delivered`, in March 2018. It does not appear in January or February.
- **`models/schema.yml` and `models/staging/schema.yml`**: someone added `delivered` to the `accepted_values` lists so the build stayed green. That test only checks that a status is known, not that it is classified correctly downstream.

I'm inferring this from the output tables. I haven't run anything against the warehouse, and finance should confirm that `delivered` counts as completed.

## 2. Fix

Define "completed" once in staging and reuse it. `customers` already reads `stg_orders`, so the change goes there.

`stg_orders.sql`:
```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status,
        status in ('completed', 'delivered') as is_completed

    from source
```

`orders.sql`: add `orders.is_completed,` to the `final` select, after `orders.status,`.

`customers.sql`:
```sql
        sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

After a rebuild, `sum(completed_lifetime_value)` should be 1103.00. The March completed total from `orders` should be 279.00 when filtering on `is_completed`. Also have finance confirm whether any other status, such as `shipped`, should count.

## 3. Tests that would have caught it

Add a singular test, `tests/assert_completed_amount_reconciles.sql`, that checks `customers` against `orders` using the same definition:
```sql
select o.completed, c.completed
from (select sum(amount) as completed from {{ ref('orders') }} where is_completed) o
cross join (select sum(completed_lifetime_value) as completed from {{ ref('customers') }}) c
where abs(o.completed - c.completed) > 0.01
```

That test only guards consistency between the two models. The real gap was that adding `delivered` to `accepted_values` required no classification decision. To close it, keep the status-to-completed mapping in a seed such as `status_map(status, is_completed)`. Then add a `relationships` test from `stg_orders.status` to that seed, so any new status fails the build until someone classifies it. Alternatively, test the count of completed orders per month against the admin export.
