claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 32 s | USD 0.007 | 2026-10-15 | project ad0b16b | prompt c1e2f11386cd

## 1. Root cause

**The bug is in `models/staging/stg_orders.sql`, which reads only `status` from `raw_orders`. The upstream feed has moved the value to a second column, `order_status`, and `raw_orders` (the seed behind it) now carries both.**

- In the `raw_orders` sample, rows with `status` NULL have `order_status` populated (`placed`, `shipped`). Rows with both populated agree (`completed`/`completed`). So the feed appears to have renamed `status` to `order_status` partway through, and newer rows only have the new column.
- `stg_orders` does `select status`, so those orders reach `orders` with a NULL status. The output confirms it: `orders` has 43 orders with NULL status totalling 761.00. The buckets sum to the expected total (824 + 38 + 49 + 761 = 1672), so no order was lost. Only the status label moved.
- `customers.sql` computes `completed_lifetime_value` with `case when orders.status = 'completed'`. A NULL status never equals `'completed'`, so completed orders with a NULL status drop out. 1103.00 − 824.00 = 279.00 is the completed value that now sits in the NULL bucket. I can't see per-order history, so that match is consistent with the cause, not proof. The check in step 2 confirms it.

**Why the tests passed:**
- `accepted_values` on `stg_orders.status` and `orders.status` runs `where status not in (...)`. For a NULL status that predicate evaluates to NULL, not true, so the row is never returned as a failure. The test passes on NULLs.
- Neither `stg_orders.status` nor `orders.status` has a `not_null` test.

**Why other outputs look right:** `daily_revenue` counts every order regardless of status, and `region_revenue` sums payments. Neither filters on status, so only the status-filtered metric moved.

## 2. Fix

First confirm which column is authoritative and that they never disagree:

```sql
select
  count(*) filter (where status is null and order_status is not null)                   as only_order_status,
  count(*) filter (where status is not null and order_status is not null and status <> order_status) as disagree,
  count(*) filter (where order_status is null)                                          as no_order_status
from raw_orders;
```

If `disagree` is 0, change `models/staging/stg_orders.sql`:

```sql
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

`order_status` is preferred because it is the populated column in the new rows. `status` is the fallback for older rows.

If `disagree` is above 0, stop and ask the feed owner which column is canonical before changing the mapping.

Then rebuild downstream:

```
dbt build --select stg_orders+
```

Expected result: `customers.completed_lifetime_value` returns to 1103.00 and `orders` has no NULL status. If the 1103.00 figure is not reproduced, the rename theory is wrong and the reconciliation needs another look.

## 3. Test that would have caught it

Add `not_null` to status in both the staging and marts schema files. `accepted_values` alone cannot catch NULLs, and this is the test that fails on today's data.

`models/staging/schema.yml`:

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

`models/schema.yml`, under `orders`:

```yaml
      - name: status
        description: '{{ doc("orders_status") }}'
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Before the fix, the build would fail with 43 rows. That is the signal that was missing.

**Other notes:**
- `models/schema.yml` documents `customers.total_order_amount`, but `customers.sql` doesn't produce that column. The docs have drifted from the model.
- `raw_orders` has no entry in the seeds section of `schema.yml`, so its columns aren't tested at the source. Adding a `not_null` for `order_status` there would catch the next rename one step earlier.
