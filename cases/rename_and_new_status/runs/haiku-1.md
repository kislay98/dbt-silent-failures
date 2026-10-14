claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 35 s | USD 0.006 | 2026-10-14 | project f769fdf+wk6ext | prompt 56722c109409

I worked from the logs and query output you pasted. I didn't run anything against the warehouse.

## 1. Root cause

The problem is in the raw data and the staging model that reads it, not in `customers.sql`.

`raw_orders` now has two status columns. `status` is populated only through order 56 (2018-02-28) and is NULL from order 57 onward. `order_status` is populated for every row, and its values match what finance and the admin see. `stg_orders.sql` still reads the old column:

```sql
-- jaffle_shop/models/staging/stg_orders.sql
        status
```

The output confirms this:

- NULL status is exactly the March and April orders: 35 + 8 = 43 orders, and 622.00 + 139.00 = 761.00 of amount. Those rows are the `NULL` line in the status breakdown.
- The 824.00 is January and February completed revenue only. 1103.00 − 279.00 (March completed, per the admin) = 824.00.
- `completed_lifetime_value` sums payments where `orders.status = 'completed'`, so orders with NULL status drop out of it. `sum(orders.amount)` doesn't use status, which is why it still reads 1672.00.

The tests passed because `accepted_values` ignores NULLs, and nothing tests `status` for `not_null`. The `daily_revenue` and `region_revenue` models don't use status, so they are unaffected.

`order_status` also contains `delivered`, which is not in the accepted list. That will matter once the staging model reads the new column.

## 2. Fix

In `stg_orders.sql`, read the populated column and handle the new `delivered` value:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        case when order_status = 'delivered' then 'completed' else order_status end as status

    from source

)
```

The `delivered` → `completed` mapping is my assumption, not something the data proves. Before merging it, check it against the admin number: after the change, `sum(amount)` for March with `status = 'completed'` should equal 279.00. If it doesn't, try without the mapping, and confirm with the ingestion owner what `delivered` means. Also check that `order_status` is never NULL before dropping the old column.

Nothing needs backfilling. `customers` and `orders` are full table rebuilds, so run:

```
dbt build --select stg_orders+
```

The ingestion job should also be fixed or the `status` column retired, so this doesn't recur.

## 3. Test that would have caught it

In `jaffle_shop/models/staging/schema.yml`, add `not_null` to `status`:

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

Add the same `not_null` test to `orders.status` in `jaffle_shop/models/schema.yml`. Once the mapping is in place, the existing `accepted_values` test will fail on any unmapped value such as a new `delivered`-style status. Without that mapping, the build would have failed on the 43 NULLs.

## Other things I noticed (not verified)

- **Seed tests didn't run.** The log shows 32 data tests found and 28 run. The 4 seed tests on `raw_customer_regions` and `raw_payment_methods` are missing, and no seed step appears. Check whether they're being excluded.
- **`raw_customer_regions` has `updated_at`, but nothing enforces one row per customer.** There's no `unique` test on `customer_id`. If it has duplicates, `region_revenue` double-counts revenue. Add `unique` and check.
- **Doc drift.** `schema.yml` documents `total_order_amount` on `customers`, but `customers.sql` doesn't produce that column.
