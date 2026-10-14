claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 15 s | USD 0.053 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`models/staging/stg_orders.sql`** reads only `status` from `raw_orders`, but the raw table now has two status columns, `status` and `order_status`. The orders from 2018-03 onward (43 of 99) have a NULL `status`, so the newer rows apparently carry their status only in `order_status`. This is schema drift in the feed. I can't see the seed data, so this is inferred from the outputs, not checked against the rows.

The evidence:
- The month breakdown shows `with_status = 0` for every 2018-03 and 2018-04 order, and full coverage before that.
- `orders` has 43 rows with `status = NULL` carrying 761.00. That matches `1672 − 824 − 38 − 49 = 761`.
- `customers.completed_lifetime_value` only sums `status = 'completed'`, so the NULL-status orders silently drop out. Presumably about 279.00 of the 761.00 (1103 − 824) is orders that are really completed.
- `sum(orders.amount)` is unchanged because `amount` doesn't depend on status.

Every test passed because:
- `accepted_values` ignores NULLs.
- There is no `not_null` test on `status` in `stg_orders` or `orders`.

## 2. Fix

In `stg_orders.sql`:

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

Before relying on it, check that `order_status` uses the same vocabulary as `status`:

```sql
select order_status, count(*) from raw_orders where status is null group by 1;
```

If the values differ (for example `complete` vs `completed`), map them in the same expression. If they are not a subset of the five accepted values, the `accepted_values` test will now fail, which is what we want.

Then run `dbt build`. `completed_lifetime_value` should return to 1103.00. `daily_revenue` doesn't use status, so it needs no full refresh.

## 3. Tests that would have caught this

Add `not_null` to `status` in both `models/staging/schema.yml` (`stg_orders`) and `models/schema.yml` (`orders`):

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Optionally, add a singular test `tests/assert_no_status_column_drift.sql` that fails if the two raw columns are populated for different rows:

```sql
select id
from {{ ref('raw_orders') }}
where status is null and order_status is not null
```

This would flag the drift directly. You'd retire it once the feed settles on one column.
