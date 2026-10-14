claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 26 s | USD 0.006 | 2026-10-15 | project ad0b16b | prompt c1e2f11386cd

## 1. Root cause

The bug is in the raw `raw_orders` feed and how `stg_orders.sql` reads it. The code is not wrong in a way that has changed.

- `raw_orders` now has two status columns: `status` and `order_status`. In the warehouse sample, `status` is NULL for 43 of 99 orders (amount 761.00), and `order_status` carries the value (`placed`, `shipped`, `completed`, ...).
- `jaffle_shop/models/staging/stg_orders.sql` only reads `status`:
  ```sql
  status
  ```
  So for those 43 orders, `stg_orders.status` is NULL.
- `jaffle_shop/models/customers.sql` computes `completed_lifetime_value` with `case when orders.status = 'completed' ...`. A NULL status evaluates to `else 0`, so any completed order whose status moved to `order_status` drops out of the completed total. The customer total (`customer_lifetime_value`) is unaffected, which is why `sum(amount)` still reads 1672.00.
- The output confirms this. `sum(completed_lifetime_value)` is 824.00, which is exactly the completed amount in `orders` (50 orders, 824.0). The model is internally consistent; its input is missing data. The 1103.00 − 824.00 = 279.00 gap is the likely amount of orders that are `completed` in `order_status` but NULL in `status`. I have not verified this split, so run the check in the fix section first.

**Why the tests passed:**
- `accepted_values` on `stg_orders.status` ignores NULLs, so 43 NULL statuses pass.
- There is no `not_null` test on `stg_orders.status` or `raw_orders.order_status`.
- Nothing compares `status` with `order_status` or cross-checks completed revenue against the source.

## 2. Fix

First confirm the shape of the feed. Run this against the warehouse:

```sql
select
    count(*) filter (where status is null)                  as status_null,
    count(*) filter (where order_status is null)            as order_status_null,
    count(*) filter (where status is not null
                       and order_status is not null
                       and status <> order_status)          as disagree,
    sum(case when status is null and order_status = 'completed'
             then amount end)                               as completed_only_in_order_status
from raw_orders
join raw_payments_by_order ... -- or compare via stg_orders/orders
```

If `order_status` is populated for all 99 rows and never disagrees with `status` where both are present, the fix is to take `order_status` as the source of truth, falling back to `status`:

```sql
-- jaffle_shop/models/staging/stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(order_status, status) as status

    from source

)
```

If `disagree` is non-zero, stop and ask the feed owner which column is authoritative before choosing an order of precedence. I have not run these queries, so the choice of `order_status` is inferred from the 10-row sample, where every row that has `status` also has an identical `order_status`.

After the change, `completed_lifetime_value` should sum to about 1103.00 (verify with `dbt build --select stg_orders+`). If it doesn't, the gap is something other than NULL statuses and the investigation continues.

The durable fix belongs upstream too: the feed should populate one status column. Until that's done, the staging coalesce is the place to handle it.

## 3. Test that would have caught this

Add tests that fail on NULL status and on disagreement between the two source columns. Put them in `jaffle_shop/models/staging/schema.yml`:

```yaml
  - name: stg_orders
    columns:
      - name: order_id
        tests:
          - unique
          - not_null
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Add a singular test for the two source columns, `jaffle_shop/tests/assert_order_status_consistent.sql`:

```sql
-- Fails if the raw feed carries two different statuses for the same order
select
    id,
    status,
    order_status
from {{ ref('raw_orders') }}
where status is not null
  and order_status is not null
  and status <> order_status
```

The `not_null` on `stg_orders.status` would have failed on this week's build with 43 rows. The singular test catches a feed where the two columns disagree, which is the condition that produced the NULLs.

A stronger guard would be a reconciliation test: `sum(amount)` over `orders` where `status = 'completed'` should equal the same sum computed directly from `raw_orders.order_status = 'completed'`. That catches silent loss from any staging rule, not just NULLs. I can write it if you want it.

Also noticed while reading, not related to this bug: `schema.yml` documents `total_order_amount` on `customers`, but the model has no such column. Remove the entry or add the column.
