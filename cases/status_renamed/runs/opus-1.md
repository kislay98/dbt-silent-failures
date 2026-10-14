claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 22 s | USD 0.113 | 2026-10-14 | project f769fdf | prompt b0cd2df9df93

## 1. Root cause

The bug is in the data, not in `customers.sql`. The `raw_orders` feed changed shape partway through: the status moved from the `status` column to a new `order_status` column.

The evidence is in your output sample:

- `information_schema.columns` shows `raw_orders` now has both `status` and `order_status`.
- The monthly breakdown shows `status` is fully populated for 2018-01 and 2018-02, and **0 of 43 rows** for 2018-03 and 2018-04. Those rows have their status only in `order_status`.
- `jaffle_shop/models/staging/stg_orders.sql` selects only `status` and never reads `order_status`. So 43 orders worth 761.00 reach `orders` with `status = NULL`.
- `customers.sql` counts only `orders.status = 'completed'`, so March and April completed orders are dropped. Completed value falls to the 824.00 from Jan–Feb. The missing 1103 − 824 = 279.00 is the completed part of the 761.00 in NULL-status orders.
- `sum(orders.amount)` is unchanged at 1672.00 because payment amounts are unaffected; only the status label is lost.

The source of this is the seed file `jaffle_shop/seeds/raw_orders.csv`, which now has an `order_status` column that is filled in for newer rows instead of `status`.

**Why no test failed:** `accepted_values` ignores NULLs, because `NULL not in (...)` evaluates to NULL and the row is filtered out. Nothing checks that `status` is not null, so all 40 checks passed.

## 2. Fix

In `jaffle_shop/models/staging/stg_orders.sql`, merge the two columns:

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

The alternative is to fix the feed at the source: rewrite `seeds/raw_orders.csv` so every row uses `status`, then drop `order_status`. If the upstream feed has permanently switched to `order_status`, prefer `coalesce(order_status, status)` so the new column wins. Before choosing an order, check that no row has both columns filled with different values:

```sql
select count(*) from raw_orders
where status is not null and order_status is not null and status <> order_status
```

After `dbt build`, `select sum(completed_lifetime_value) from customers` should return 1103.00 again. I haven't run this, so please confirm it. If it doesn't match, compare `select order_status, sum(amount) from orders join raw_orders ... where status is null group by 1` against last week's report.

## 3. Tests that would have caught it

Add `not_null` next to `accepted_values` on status, in both schema files:

```yaml
# models/staging/schema.yml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

```yaml
# models/schema.yml (orders.status)
        tests:
          - not_null
          - accepted_values: ...
```

With the current data, this would have failed with 43 rows.

As an optional extra guard against this kind of column drift, add a singular test, `tests/raw_orders_status_consistent.sql`, that fails if the two columns ever disagree:

```sql
select *
from {{ ref('raw_orders') }}
where status is not null
  and order_status is not null
  and status <> order_status
```

Declaring column types for `raw_orders` under `seeds:` in `models/schema.yml`, or using a model contract on `stg_orders`, would also make schema changes visible instead of silent.

**Unrelated issue:** `raw_customer_regions` has an `updated_at` column and only a `not_null` test on `customer_id`. If that feed ever sends more than one row per customer, the join in `region_revenue.sql` will multiply revenue. It is worth adding a `unique` test on `raw_customer_regions.customer_id`.
