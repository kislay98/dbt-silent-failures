claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 22 s | USD 0.111 | 2026-10-14 | project f769fdf | prompt b0cd2df9df93

## 1. Root cause

**The `raw_orders` seed changed shape, and `stg_orders` still reads only the old column.**

- The information_schema output shows `raw_orders` now has two status columns: `status` and `order_status`. Last week it had only `status`.
- The by-month query shows the split. Every order from 2018-01 and 2018-02 has `status`. Every order from 2018-03 and 2018-04 (43 orders) has `status = NULL`, so those rows must carry their value in the new `order_status` column. The upstream feed renamed the field partway through the data, and `seeds/raw_orders.csv` was reloaded with both headers.
- `models/staging/stg_orders.sql` selects only `status`, so all 43 newer orders reach `orders` and `customers` with a NULL status.
- In `models/customers.sql`, `case when orders.status = 'completed'` is false for NULL. Completed March and April orders therefore drop out of `completed_lifetime_value`. The lost amount is 1103 − 824 = 279 AUD of the 761 AUD now sitting in the NULL bucket.
- `sum(orders.amount)` is unchanged at 1672 because amounts don't depend on status.

**Why every test passed:** `accepted_values` compiles to `where status not in (...)`, and `NULL not in (...)` evaluates to NULL, not true. NULLs are therefore never flagged. No `not_null` test exists on `status` in either `stg_orders` or `orders`.

Before fixing, check what the new column actually contains, since the feed may also have changed its vocabulary (e.g. `'COMPLETED'`, `'complete'`):

```sql
select status, order_status, count(*) from raw_orders group by all order by all;
```

You should see exactly one of the two columns populated on every row.

## 2. Fix

The best fix is at the source: have the feed and seed use one `status` column. To make the build correct now regardless, coalesce the two in `models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- the feed renamed `status` to `order_status` from 2018-03 onwards
        coalesce(status, order_status) as status

    from source

)
```

If the check above shows different values in `order_status`, normalize them here too (e.g. `lower(trim(...))` or a mapping `case`). Then rerun `dbt build`. The status breakdown should have no NULL row, and `sum(completed_lifetime_value)` should return to 1103.00.

## 3. Tests that would have caught it

Add `not_null` to `status` in `models/staging/schema.yml`, and the same in `models/schema.yml` for `orders`:

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

On this week's data, `not_null_stg_orders_status` fails with 43 rows and blocks `orders` and `customers` from building. Together with `accepted_values`, it also catches a vocabulary change in `order_status` once the coalesce is in place.

Optionally, guard against schema drift in the seed itself with a singular test, `tests/raw_orders_single_status_column.sql`:

```sql
-- fails if the feed carries status in neither or both columns
select *
from {{ ref('raw_orders') }}
where (status is null) = (order_status is null)
```

A column-set check on the seed would be another option, such as dbt-expectations' `expect_table_columns_to_match_set` or a pinned `column_types` in `dbt_project.yml`. That would flag the new header the moment it appeared.
