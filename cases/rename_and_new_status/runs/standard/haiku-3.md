claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 32 s | USD 0.006 | 2026-10-15 | project ad0b16b | prompt c4a4ba955988

## 1. Root cause

`stg_orders.sql` maps `status` from `raw_orders.status`, but the warehouse's `raw_orders` now also has an `order_status` column. The `status` column is NULL for 43 of the 99 orders, so those orders reach `orders`, `customers`, and every downstream model with no status.

The numbers fit:

- `orders` has 50 completed orders worth 824.00, 43 orders with NULL status worth 761.00, and 6 others worth 87.00. That totals 1672.00, matching `sum(orders.amount)`.
- Last week's 1103.00 minus today's 824.00 is 279.00, which is the March completed revenue the admin shows. Those orders are almost certainly in the NULL-status group.
- `customers.customer_lifetime_value` (1672.00) is built from payments, not status, so it is unaffected. Only the completed figures drop.
- The `raw_orders` sample shows the pattern: rows with `status = NULL` and `order_status = 'placed'` or `'delivered'`, and rows where both are `'completed'`.

Why the tests passed: `accepted_values` ignores NULLs, and nothing tests `stg_orders.status` or `orders.status` for `not_null`. A NULL status passes every test in the project.

The change happened in the source, not in the dbt code. I'm inferring that from the fact that last week's number was correct and the models have not changed. The ingestion job's schema change is the likely trigger; I can't see its history to confirm.

Two side notes:

- The log says "32 data tests" were found but "28" ran. The 4 missing are almost certainly the `not_null` tests on `raw_customer_regions` and `raw_payment_methods`, because the seeds are not part of this build. Those tests have never run in the nightly job.
- `customers` documents a `total_order_amount` column that does not exist. It's documentation drift, not the cause.

## 2. Fix

Confirm first which column is authoritative and what `'delivered'` means. Then run this in the warehouse:

```sql
select order_status, count(*), round(sum(amount), 2)
from orders o
join raw_orders r on r.id = o.order_id
where status is null
group by 1;
```

If the `'completed'` rows sum to 279.00 and the existing 50 completed orders plus those give 1103.00, then `order_status` is the right source and `'delivered'` is not counted as completed. In that case, fix staging:

```sql
-- stg_orders.sql
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

If `'delivered'` rows should count as completed, add a mapping for it. Confirm the business rule with finance before choosing. Once `'delivered'` is in the status column, the existing `accepted_values` test will fail on it, which is the test working as intended.

Then rebuild `stg_orders`, `orders`, `customers`, and `region_revenue`. `dbt build --select stg_orders+` will do it.

## 3. A test that would have caught it

Add `not_null` to `status` on both the staging and mart models. `accepted_values` alone can't catch NULLs:

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
# models/schema.yml, under orders.columns
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

For a stronger guard, add a singular test that fails if the staging status differs from the source's `order_status`. Also add the seed-based tests to the nightly run, so the 4 missing tests execute.
