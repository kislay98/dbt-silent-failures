claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 18 s | USD 0.105 | 2026-10-14 | project f769fdf | prompt b0cd2df9df93

## 1. Root cause

**The upstream orders feed renamed `status` to `order_status` partway through the data, and `models/staging/stg_orders.sql` only reads `status`.**

The evidence:

- `raw_orders` now has **both** `status` and `order_status` (from the `information_schema` output). The `raw_orders` seed CSV gained an `order_status` column.
- By month, `count(status)` is 29/29 for 2018-01 and 27/27 for 2018-02, but **0/35 for 2018-03 and 0/8 for 2018-04**. Every order from March onward has its status in `order_status`, and `status` is empty.
- `stg_orders.sql` selects `status`, so those 43 orders reach `orders` with `status = NULL`. That matches the `NULL | 43 | 761.0` row.
- `customers.sql` computes `completed_amount` with `case when orders.status = 'completed'`. The NULL-status orders fall into `else 0`, so completed revenue drops. `sum(amount)` doesn't depend on status, so it stays at 1672.00. The missing 279.00 (1103 − 824) is the completed share of those 761.00.

**Why every test passed:** `accepted_values` ignores NULLs, so NULL statuses pass it. Nothing in the project asserts `status` is `not_null`.

## 2. Fix

In `models/staging/stg_orders.sql`, read whichever column is populated:

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

Before shipping, check that the new column uses the same vocabulary (not `'Completed'`, `'COMPLETED'`, etc.) and that no row has both columns set with different values:

```sql
select status, order_status, count(*)
from raw_orders
group by 1, 2
order by 1, 2;
```

If the vocabulary differs, normalize it, for example with `lower(trim(...))` or a mapping `case`. After `dbt build`, `sum(completed_lifetime_value)` should be back at 1103.00, and the `NULL` row in the status breakdown should disappear.

Longer term, ask the feed owner which column is canonical. If `order_status` is the new name, backfill it for the old rows and drop `status`, or keep the `coalesce` in staging as the single place that handles the rename.

## 3. Tests that would have caught it

Add `not_null` on `status` in `models/staging/schema.yml`. This one test would have failed the build on 43 rows:

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

Add the same `not_null` to `orders.status` in `models/schema.yml`.

Optionally, add a singular test that catches the two source columns disagreeing, `tests/assert_raw_orders_status_consistent.sql`:

```sql
select *
from {{ ref('raw_orders') }}
where status is not null
  and order_status is not null
  and status <> order_status
```

The general lesson is that `accepted_values` (and `relationships`) silently skip NULLs. Any column that drives business logic, like `status` here, needs an explicit `not_null` next to it.
