claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 23 s | USD 0.067 | 2026-10-14 | project f769fdf+wk6ext | prompt 56722c109409

## 1. Root cause

The ingestion job changed the shape of `raw_orders` in March. Orders from id 57 (2018-03-01) onward have `status` NULL, and the real value is in a new column, `order_status`. `models/staging/stg_orders.sql` still reads only `status`, so 43 orders (March and April, 761.00 of amount) come through with `status = NULL`.

`customers.sql` computes `completed_lifetime_value` with `case when orders.status = 'completed'`. The NULL orders never match, so they count as not completed. This accounts for the whole gap:

- 1103.00 - 824.00 = 279.00, which is exactly the March completed total in the shop admin.
- `sum(orders.amount)` is unchanged at 1672.00, because amounts come from payments and don't depend on status.
- 761.00 is the amount of the 43 NULL-status orders, which matches the NULL row in the status breakdown (622 for March + 139 for April).

All 35 checks passed because of how the tests are written:

- `accepted_values` on `status` ignores NULLs.
- No `not_null` test exists on `status`, in either `stg_orders` or `orders`.
- Nothing reconciles the output against the source.

The new column also has a value the project has never seen, `delivered` (ids 66 and 67). It is not in the `accepted_values` list, and `stg_orders` never reads the column, so no test could fire on it. It is likely the new name for `completed`, since those orders fall between the completed and shipped ranges, but I can't confirm that from the sample. The shop admin's March completed figure of 279.00 should be the check.

## 2. Fix

In `models/staging/stg_orders.sql`, read both columns and normalize the new vocabulary:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        case coalesce(status, order_status)
            when 'delivered' then 'completed'   -- new feed's name for completed; confirm with the feed owner
            else coalesce(status, order_status)
        end as status

    from source
```

Then run `dbt build`. Before relying on the `delivered` mapping, check that `sum(amount)` for orders with `order_status in ('completed','delivered')` in March comes to 279.00. If it does, `sum(customers.completed_lifetime_value)` should return to 1103.00, and `orders.amount` stays at 1672.00. If the March total is off, `delivered` is not simply `completed`, and the mapping needs to change.

`daily_revenue` doesn't use status, so it needs no change. If ingestion eventually drops the old `status` column, `stg_orders` will need updating again.

## 3. Tests that would have caught it

Add `not_null` to `status` in both `models/staging/schema.yml` (for `stg_orders`) and `models/schema.yml` (for `orders`):

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

With that test, the March load would have failed with 43 rows. The unmapped `delivered` value would also have failed `accepted_values` once the column was read.

Add a reconciliation test, `tests/assert_completed_ltv_matches_orders.sql`:

```sql
select o.completed_amount, c.completed_ltv
from (select sum(amount) as completed_amount from {{ ref('orders') }} where status = 'completed') o
cross join (select sum(completed_lifetime_value) as completed_ltv from {{ ref('customers') }}) c
where abs(o.completed_amount - c.completed_ltv) > 0.005
```

This test would not have caught the bug, because both sides use the same broken status. A more useful form compares against the raw source, such as `raw_orders` joined to payments with `coalesce(status, order_status) in ('completed','delivered')`. A source-level `accepted_values` test on `raw_orders.order_status` would also catch new vocabulary.
