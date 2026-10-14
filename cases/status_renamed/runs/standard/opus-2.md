claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 18 s | USD 0.168 | 2026-10-15 | project ad0b16b | prompt c1e2f11386cd

## 1. Root cause

The upstream orders feed changed shape. `raw_orders` now has two status columns, `status` and a new `order_status`, and the feed has stopped filling `status` for newer orders:

```
id  │ ordered_at          │ status    │ order_status
84  │ 2018-03-26 12:34:27 │ NULL      │ placed
78  │ 2018-03-23 12:57:20 │ NULL      │ shipped
33  │ 2018-02-04 18:13:15 │ completed │ completed
```

`jaffle_shop/models/staging/stg_orders.sql` still selects only `status`. So 43 of the 99 orders (roughly March onward) reach `orders` and `customers` with `status = NULL`:

```
completed      │ 50 │ 824.0
NULL           │ 43 │ 761.0   ← includes the orders that are actually 'completed'
```

In `customers.sql`, `case when orders.status = 'completed' then amount else 0 end` treats NULL as "not completed". Those orders drop out of `completed_lifetime_value` and it falls from 1103 to 824, a 279.00 gap. `amount` and `customer_lifetime_value` don't depend on status, so they stay at 1672. That matches what finance sees.

No test failed because the only status test is `accepted_values`, and dbt's `accepted_values` ignores NULLs. Nothing asserts that `status` is not null, and nothing checks the raw columns.

## 2. Fix

In `jaffle_shop/models/staging/stg_orders.sql`, read the new column and fall back to the old one:

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

Before shipping, check that the two columns never disagree when both are filled, and that the result restores 1103:

```sql
-- should return 0 rows
select * from raw_orders
where status is not null and order_status is not null and status <> order_status;

-- should be 1103.00
select sum(p.amount) / 100.0
from raw_payments p join raw_orders o on p.order_id = o.id
where coalesce(o.order_status, o.status) = 'completed';
```

If the feed owner confirms `status` is deprecated, switch to `order_status as status` and drop the coalesce. Then rerun `dbt build`. `orders` and `customers` will pick up the corrected status. `daily_revenue` and `region_revenue` don't use status, so they aren't affected.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add `not_null` to the status column. This alone would have failed with 43 rows:

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

Optionally, add a singular test that catches the two source columns drifting apart, `tests/assert_raw_order_status_consistent.sql`:

```sql
select *
from {{ ref('raw_orders') }}
where status is distinct from order_status
  and status is not null
  and order_status is not null
```

A side note: `models/schema.yml` documents a `customers` column called `total_order_amount`, but the model outputs `customer_lifetime_value`. It's unrelated to this bug, but worth fixing while you're in the file.
