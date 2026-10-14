claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 23 s | USD 0.004 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

The bug is in `jaffle_shop/models/staging/stg_orders.sql`. It reads `status` from `raw_orders`, but `raw_orders` now has two status columns: `status` and `order_status`. The raw table has been changed to a new schema, and `stg_orders` still reads the old column.

The output shows where the old column stops being populated:

| month   | orders | with_status |
|---------|--------|-------------|
| 2018-01 | 29     | 29          |
| 2018-02 | 27     | 27          |
| 2018-03 | 35     | **0**       |
| 2018-04 | 8      | **0**       |

Every order from 2018-03 onward has `status = NULL` (43 orders, $761). The `completed` bucket drops to $824 for the same reason:

- `customers.completed_lifetime_value` sums amounts only where `status = 'completed'`, so NULL-status orders silently drop out.
- $824 + $279 = $1103, which matches last week's report. That means $279 of the NULL-status revenue was completed-status before the change. I can't verify the exact split without last week's snapshot, but the arithmetic fits.
- `sum(orders.amount)` is still 1672 because `amount` comes from payments and is not affected by status.

Why no test failed: `accepted_values` ignores NULLs, so NULL statuses pass. Nothing in the project tests `status` for `not_null`, and `stg_orders.status` has no `not_null` test at all.

I can't see the values in `order_status`, so I'm inferring that it is the replacement column. The verification step below confirms it.

## 2. Fix

First, confirm which column is authoritative:

```sql
select
    strftime(ordered_at, '%Y-%m') as month,
    count(*)                     as rows,
    count(status)                as status_populated,
    count(order_status)          as order_status_populated,
    sum(case when status is not null and order_status is not null
              and status <> order_status then 1 else 0 end) as disagreements
from raw_orders
group by 1 order by 1;
```

If `order_status` is populated for every row, and its values agree with `status` where both are populated, change the staging model:

```sql
-- jaffle_shop/models/staging/stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        order_status as status

    from source

)
```

If `status` is still populated for older rows and `order_status` is not, use `coalesce(order_status, status) as status` instead, after checking the disagreement count is zero. Then rebuild downstream models:

```
dbt build --select stg_orders+
```

Expected result: `completed_lifetime_value` should return to 1103.00 and `orders.status` should have no NULLs.

Don't edit the seed CSV. It is test input, and the staging model is where the schema mapping belongs.

## 3. Test that would have caught it

Add `not_null` to `status` in both the staging and marts schema files. The `accepted_values` test alone cannot catch NULLs.

```yaml
# jaffle_shop/models/staging/schema.yml
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
# jaffle_shop/models/schema.yml, under orders.columns
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

This test fails the build on the current data, which is the point. It fails on the first run after the schema change.

Also consider a singular test that guards the finance figure directly, for example `tests/assert_completed_revenue_reconciles.sql`, which fails if completed revenue plus other statuses doesn't equal total amount. That would catch a NULL-status regression even if someone removed the `not_null` test later.

## Other issues I noticed (not the cause)

- `customers.yml` documents `total_order_amount`, but `customers.sql` doesn't produce that column. The doc is out of date.
- `raw_customer_regions` has an `updated_at` column, which suggests multiple rows per customer over time. `region_revenue` joins on `customer_id`, so duplicate rows would double-count revenue. The seed currently has 100 rows for 100 customers, so there is no fan-out today. Add a `unique` test on `raw_customer_regions.customer_id`, or dedupe to the latest `updated_at` in the model.
