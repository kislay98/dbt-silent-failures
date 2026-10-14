claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 18 s | USD 0.164 | 2026-10-15 | project ad0b16b | prompt c1e2f11386cd

## 1. Root cause

The problem is in the source data, `seeds/raw_orders.csv`, together with `models/staging/stg_orders.sql`.

`raw_orders` now has two status columns: `status` and a new `order_status`. For the newer orders (from about March 2018 onward), the feed fills in only `order_status` and leaves `status` NULL:

```
id 84 │ status NULL      │ order_status placed
id 78 │ status NULL      │ order_status shipped
id 33 │ status completed │ order_status completed
```

`stg_orders.sql` still reads `status`, so 43 orders reach `orders` and `customers` with `status = NULL`:

```
completed      │ 50 │ 824.0
NULL           │ 43 │ 761.0   ← completed orders are hidden in here
```

In `customers.sql`, `case when orders.status = 'completed'` is false for NULL. Every completed order in that group is dropped from `completed_lifetime_value`. That explains why the total fell from 1103 to 824, a gap of 279, while `sum(amount)` stayed at 1672. `daily_revenue` and `region_revenue` don't use status, so they still look right.

No test failed because `accepted_values` ignores NULLs, and nothing checks that `status` is not null.

## 2. Fix

In `models/staging/stg_orders.sql`, prefer the new column and fall back to the old one:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(order_status, status) as status

    from source
```

After `dbt build`, check the result:

```sql
select status, count(*), sum(amount) from orders group by 1;
-- expect no NULL row; completed should be back to 1103
select sum(completed_lifetime_value) from customers;  -- expect 1103.00
```

I haven't run this, because I only have the samples you pasted, not the seed file. If `completed` doesn't come back to 1103, compare the rows where both columns are filled in. There may be cases where `status` and `order_status` disagree, and those would need to be resolved with the feed owner. Once the feed has fully moved to `order_status`, you can switch to that column alone.

## 3. Tests that would have caught it

In `models/staging/schema.yml` (and the same for `orders.status` in `models/schema.yml`):

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

`not_null` would have failed on 43 rows in this build.

A singular test that catches the old and new columns disagreeing, `tests/assert_raw_order_status_consistent.sql`:

```sql
select id, status, order_status
from {{ ref('raw_orders') }}
where status is distinct from order_status
  and status is not null
  and order_status is not null
```

It would also help to add a check on the `customers` totals (for example, completed plus non-completed value should equal lifetime value, split by status). That would catch amounts dropping out of a status bucket even when no column is NULL.

A side note: `models/schema.yml` documents a `total_order_amount` column on `customers`, but the model outputs `customer_lifetime_value`. That doesn't cause this bug, but the docs should be updated to match.
