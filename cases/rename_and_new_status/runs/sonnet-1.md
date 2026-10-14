claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 23 s | USD 0.067 | 2026-10-14 | project f769fdf+wk6ext | prompt 56722c109409

## 1. Root cause

The ingestion job changed the shape of `raw_orders` partway through the data, and `stg_orders` still reads only the old column.

- `raw_orders` now has two status columns: the old `status` and a new `order_status`.
- Orders up to id 56 (through 2018-02-28) have `status` filled in.
- From order 57 (2018-03-01) onward, `status` is `NULL` and the value is only in `order_status`. That is 43 orders, matching the `NULL` row in the output (761.00 of amount).
- `models/staging/stg_orders.sql` selects only `status`, so those 43 orders get a NULL status in `orders`.
- `models/customers.sql` computes `completed_amount` with `case when orders.status = 'completed'`. A NULL status never matches, so every March/April completed order drops out of `completed_lifetime_value`.
- That is why completed revenue fell from 1103 to 824. `sum(orders.amount)` is unchanged at 1672 because amounts don't depend on status.
- The new feed also uses a different vocabulary. Orders 66 and 67 (March 10–11) have `order_status = 'delivered'`, which isn't in the accepted list. Judging by their dates, between completed orders on March 3 and shipped orders from March 14, `delivered` looks like the new name for `completed`. I couldn't check this against the full data. The 279.00 of March completed orders in the shop admin should reconcile once it is mapped, and the ingestion owners should confirm the meaning.

Every test still passed because of how the tests are written:
- `accepted_values` ignores NULLs by design.
- There is no `not_null` test on `status` in `stg_orders` or `orders`.
- The `not_null` tests on `orders` only cover the amount columns and the ids.

## 2. Fix

In `models/staging/stg_orders.sql`, read both columns and normalize the new vocabulary:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        case coalesce(status, order_status)
            when 'delivered' then 'completed'
            else coalesce(status, order_status)
        end as status

    from source

)
```

`stg_orders` is a view and `customers` and `orders` are tables, so a plain `dbt build` repairs everything. `daily_revenue` doesn't use status, so it needs no backfill.

After the rebuild, `sum(completed_lifetime_value)` should return to 1103.00. I haven't run this, so please confirm that figure. If it doesn't match, the `delivered` mapping is the thing to revisit.

Longer term, ask the ingestion owners to stop splitting the field, or to document the `order_status` vocabulary.

## 3. Tests that would have caught it

Add `not_null` to `status` in both schema files, because `accepted_values` alone lets NULLs through:

```yaml
# models/staging/schema.yml (stg_orders) and models/schema.yml (orders)
- name: status
  tests:
    - not_null
    - accepted_values: ...   # unchanged
```

Add a singular test, `tests/assert_raw_orders_status_recognised.sql`, that checks the raw feed directly and covers both columns:

```sql
-- every raw order must resolve to a known status, whichever column carries it
select id, status, order_status
from {{ ref('raw_orders') }}
where coalesce(status, order_status) is null
   or coalesce(status, order_status) not in
      ('placed', 'shipped', 'completed', 'return_pending', 'returned', 'delivered')
```

Before the fix, the existing `accepted_values` test on `stg_orders` would have failed on `delivered`, but only once the `coalesce` is in place. The `not_null` test would have failed immediately with 43 rows when the feed changed.

## Side observation

`region_revenue` joins `raw_customer_regions`, which has an `updated_at` column. If that feed holds more than one row per customer, the join would duplicate revenue. It isn't related to this bug, but it is worth a `unique` test on `customer_id` in that seed.
