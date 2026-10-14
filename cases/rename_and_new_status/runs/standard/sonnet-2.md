claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 20 s | USD 0.087 | 2026-10-15 | project ad0b16b | prompt c4a4ba955988

## 1. Root cause

The ingestion job changed `raw_orders`. It now loads a new column, `order_status`, and leaves the old `status` column NULL for newer orders. In the March-onward orders `status` is NULL and `order_status` holds the real value (`placed`, `delivered`, `completed`). The old rows still have both columns filled.

`models/staging/stg_orders.sql` only reads the old column (`status` in the `renamed` CTE), so those orders get `status = NULL`. Every downstream model then silently treats them as not completed:

- `customers.sql` uses `case when orders.status = 'completed'`. NULL fails that comparison, so those orders fall to the `else 0` branch.
- In `orders`, 43 rows (761.00) now have `status` NULL.
- 824.00 is the completed total from the old rows. The 279.00 gap is the March orders that were completed according to `order_status`, and 824 + 279 = 1103.

`sum(orders.amount)` is still 1672.00 because the amount never depended on status.

No test failed because nothing tests for NULL:
- `accepted_values` ignores NULLs by design.
- `status` has no `not_null` test in either `schema.yml`.

`daily_revenue` and `region_revenue` don't use status, so they are unaffected. They are not the cause.

I couldn't run anything against the warehouse. Which `order_status` values the 279.00 corresponds to is inferred, so please check it with the query in step 2.

## 2. Fix

In `models/staging/stg_orders.sql`, read both columns and normalise the new vocabulary:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(
            status,
            case order_status
                when 'delivered' then 'completed'
                else order_status
            end
        ) as status

    from source

)
```

The mapping `delivered` → `completed` is an assumption. `delivered` isn't in the accepted list, and `shipped` is a different state. Before merging, confirm with the data owner that the ingestion job's `delivered` means `completed`, and check that this query returns 279.00 for March:

```sql
select order_status, count(*), sum(p.amount)
from raw_orders o join orders p on p.order_id = o.id
where o.status is null group by 1
```

If it doesn't, adjust the mapping. After a rebuild, `sum(completed_lifetime_value)` should be 1103.00. Also ask the ingestion owners whether `status` is being retired, so you can drop the fallback later.

## 3. Tests that would have caught it

Add to `models/staging/schema.yml`:

```yaml
  - name: stg_orders
    columns:
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Add a singular test, `tests/assert_raw_orders_status_columns_agree.sql`, which catches the source drift itself:

```sql
-- fails if the legacy and new status columns disagree, or if either is missing
select id, status, order_status
from {{ ref('raw_orders') }}
where status is null and order_status is null
   or status is not null and order_status is not null and status <> order_status
```

Optionally, add a reconciliation test that `sum(completed_lifetime_value)` equals the sum of `orders.amount` where `status = 'completed'`. That would have flagged the 824 vs 1103 gap directly.
