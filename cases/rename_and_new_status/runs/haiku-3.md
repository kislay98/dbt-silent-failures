claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 25 s | USD 0.004 | 2026-10-14 | project f769fdf+wk6ext | prompt 56722c109409

## 1. Root cause

**`jaffle_shop/models/staging/stg_orders.sql`** reads the wrong column. The ingestion job now loads orders with two status columns, and the staging model only reads the old one:

- `raw_orders.status` is NULL for orders from 2018-03-01 onward. The sample shows this for ids 57–95, and `orders` has 43 rows with NULL status.
- `raw_orders.order_status` holds the real value for every row, including the March orders (e.g. ids 57–61 are `completed`).

`stg_orders` selects `status` and ignores `order_status`, so every March order is treated as "no status." Those orders drop out of `completed_lifetime_value` in `customers.sql`, which only sums amounts where `orders.status = 'completed'`.

The numbers match:
- Completed orders with a status value: 824.00 (the current `customers` total).
- Last week: 824.00 + 279.00 of March completed orders that had a status = 1103.00, the figure in the admin and last week's report.
- `sum(orders.amount)` is unchanged at 1672.00 because `amount` does not depend on status.

**Why the build passed:** `accepted_values` on `stg_orders.status` (in `staging/schema.yml` and `orders`' schema) ignores NULLs, and no `not_null` test exists on `status` in either model. The same gap hides a new status, `delivered` (ids 66, 67), which is not in the accepted list and would fail if the test saw it.

## 2. Fix

In `jaffle_shop/models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- raw `status` is NULL for orders from 2018-03-01 on; `order_status` is populated for all rows
        coalesce(order_status, status) as status

    from source

)
```

Before merging, check the fix against the admin figure. Sum `amount` where `order_status = 'completed'` since January. It should be 1103.00, with 279.00 in March. If it does not match, stop and check the mapping.

**Open decision:** `delivered` (ids 66, 67) is not in the accepted values. I have not mapped it to `completed`, because I can't tell from the data whether the business counts it as completed. Once the fix is in, the `accepted_values` test will fail on `delivered`. That failure is correct: someone needs to decide whether it maps to `completed` or belongs in the accepted list.

The ingestion team should also confirm which column is authoritative going forward and whether `status` will be dropped.

## 3. Test that would have caught this

In `jaffle_shop/models/staging/schema.yml`, add `not_null` to `status` on `stg_orders`, and add the same to `orders.status` in `models/schema.yml`:

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

With this test, the nightly build would have failed on the 43 NULL statuses before any table was built. `accepted_values` alone does not catch NULLs, so the `not_null` is what closes the gap.

## Other things I noticed

- **`region_revenue` may double-count revenue.** `raw_customer_regions` has an `updated_at` column, which suggests more than one row per customer. The join `orders.customer_id = regions.customer_id` would then multiply payments. Check that `customer_id` is unique in the seed, and add a `unique` test on it.
- **`customers.yml` documents `total_order_amount`,** which the model does not produce. The column is `customer_lifetime_value`.

I haven't changed any files. Should I apply the `stg_orders` and schema changes?
