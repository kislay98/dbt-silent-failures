claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 15 s | USD 0.054 | 2026-10-22

## 1. Root cause

**File:** `models/staging/stg_orders.sql`, reading the `raw_orders` seed.

The raw orders feed now has two status columns, `status` and `order_status`. For the newer orders (2018-03 and 2018-04, 43 orders), `status` is NULL and the value is presumably in `order_status` instead. The output sample shows this: orders from 2018-01 and 2018-02 all have a status (29/29 and 27/27), while 2018-03 and 2018-04 have none (0/35 and 0/8).

`stg_orders` only selects `status`, so those 43 orders get `status = NULL` all the way through `orders` (761.00 total amount) and `customers`. In `customers.sql`, `case when orders.status = 'completed'` is never true for a NULL, so those orders add 0 to `completed_lifetime_value`. That is why completed revenue dropped from 1103.00 to 824.00 while `sum(orders.amount)` and `customer_lifetime_value` stayed at 1672.00.

All tests passed because:
- `accepted_values` ignores NULLs, so the NULL statuses are not flagged.
- Neither `stg_orders.status` nor `orders.status` has a `not_null` test.

`daily_revenue` and `region_revenue` don't use status and are not affected. The `customers` model doc lists `total_order_amount`, but the column is `customer_lifetime_value`. That's a separate documentation mismatch.

## 2. Fix

Change `stg_orders.sql` to fall back to the new column:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(status, order_status) as status

    from source

)
```

Then run `dbt build`. `orders` and `customers` are tables, so they rebuild fully. I haven't seen the seed data, so first confirm two things:
- `select distinct order_status from raw_orders` uses the same vocabulary as `status` (`placed`, `shipped`, `completed`, `return_pending`, `returned`).
- No row has both columns populated with different values.

If the vocabulary differs, add a mapping. After the fix, `completed_lifetime_value` should come back to roughly the 1103.00 from last week's report. The `accepted_values` tests on `stg_orders` will then check the coalesced values, so any unmapped value fails the build.

## 3. Tests that would have caught it

Add `not_null` to the status columns in both schema files:

```yaml
# models/staging/schema.yml
  - name: stg_orders
    columns:
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

```yaml
# models/schema.yml, orders.status
        tests:
          - not_null
          - accepted_values: ...
```

With the old code this would have failed with 43 rows.

Optionally, add a singular test that fails if the number of orders with a NULL status grows. `tests/assert_no_unmapped_raw_status.sql`:

```sql
select id
from {{ ref('raw_orders') }}
where status is null and order_status is null
```

This catches the case where neither column carries a status.
