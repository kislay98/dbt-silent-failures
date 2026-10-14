claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 32 s | USD 0.125 | 2026-10-14 | project f769fdf+wk6ext | prompt 90dc0b383fa7

## 1. Root cause

The ingestion job changed the shape of `raw_orders`, and `jaffle_shop/models/staging/stg_orders.sql` still reads the old column.

- `raw_orders` now has a new column, `order_status`, next to the old `status`.
- Orders up to the end of February have both columns filled with the same value (see ids 54–56).
- From 2018‑03‑01 onward (id 57+), `status` is **NULL** and the real value is only in `order_status`.
- `stg_orders.sql` selects `status`, so every order from March and April has `status = NULL` in `stg_orders` → `orders` → `customers`. This matches the first output sample: `with_status = 0` for 2018‑03 and 2018‑04.
- `customers.sql` computes `case when orders.status = 'completed' ...`. A NULL status falls to `else 0`, so March's completed orders disappear from `completed_lifetime_value`:
  - 1103.00 − 824.00 = **279.00**, which is exactly the March completed revenue the shop admin shows.
  - `sum(orders.amount)` is unchanged at 1672.00 because amounts don't depend on status.

**Why every test passed:**
- `accepted_values` only flags non-null values outside the list. `NULL not in (...)` evaluates to NULL, not true, so NULL rows are never returned as failures.
- There is no `not_null` test on `status` in `stg_orders` or `orders`.

**A second problem is hidden behind the NULLs:** `order_status` contains a new value, `delivered` (orders 66 and 67), which is not in the accepted list. Once the column is fixed, the `accepted_values` tests will start failing until this value is handled.

## 2. Fix

In `jaffle_shop/models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- ingestion moved status to order_status from 2018-03; status is null on newer rows
        coalesce(order_status, status) as status

    from source

)
```

Then handle `delivered`. Someone should confirm with finance or the shop team whether `delivered` counts as completed revenue. The admin's 279.00 for March suggests it does not, since `completed` still exists as its own value (orders 57–61). If so, add it to the accepted values in both `models/staging/schema.yml` and `models/schema.yml`:

```yaml
values: ['placed', 'shipped', 'delivered', 'completed', 'return_pending', 'returned']
```

After `dbt build`, check that `select sum(completed_lifetime_value) from customers` returns 1103.00.

## 3. Tests that would have caught it

Add `not_null` to `status` in both `stg_orders` and `orders`. This alone would have failed on 43 rows:

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'delivered', 'completed', 'return_pending', 'returned']
```

Optionally, add a singular test that catches the two columns drifting apart while the migration is in progress. Save it as `tests/raw_orders_status_consistent.sql`:

```sql
-- fails if the old and new status columns disagree, or both are empty
select *
from {{ ref('raw_orders') }}
where (status is not null and order_status is not null and status <> order_status)
   or (status is null and order_status is null)
```

A reconciliation test on revenue would also catch this class of bug, where completed revenue silently drops even though row counts stay the same. For example, compare month-over-month completed revenue for closed months against a stored snapshot, or against the admin export.
