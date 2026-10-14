claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 22 s | USD 0.089 | 2026-10-15 | project ad0b16b | prompt 1b2ae9e32b52

## 1. Root cause

The `delivered` status is not counted as completed, so finance's "completed" is understated by the 136.00 of delivered orders. The figures reconcile exactly:

| | Amount |
|---|---|
| Admin, since January | 1103.00 |
| `orders` with `status = 'completed'` | 967.00 |
| Difference | 136.00 |
| `orders` with `status = 'delivered'` | 136.00 (8 orders) |

March works the same way: 279.00 in the admin, 143.00 `completed` in `orders`, and 136.00 difference. All 8 delivered orders therefore fall in March. Total revenue matches in every month because nothing is lost; the March orders are just sitting under a different status label.

The evidence points to the source system having started emitting `delivered` in March for orders that are finished. `completed` still appears in March, so both labels coexist. I inferred this from the numbers, so confirm it with the ingestion or shop owner.

The files involved:
- **`models/schema.yml` and `models/staging/schema.yml`:** `accepted_values` on `status` lists `delivered`. It was widened to include the new value so the test stays green, which is why all 35 checks pass.
- **`models/customers.sql`:** hardcodes `orders.status = 'completed'` in `completed_amount`, so `completed_lifetime_value` is 967.00. This is the same flaw, repeated.
- **`models/orders.sql` and `models/staging/stg_orders.sql`:** pass `status` through raw. There is no single definition of "completed", so every consumer hardcodes its own.

The other models are fine. Payment cents are converted correctly, `region_revenue` totals 1672.00 and matches `customer_lifetime_value`, and the fee joins are 1:1.

## 2. Fix

Define "completed" once, in staging, and use it everywhere.

`stg_orders.sql`:
```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status,
        status in ('completed', 'delivered') as is_completed

    from source
```

`orders.sql` final CTE: add `orders.is_completed,` after `orders.status,`.

`customers.sql`:
```sql
        sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

After a rebuild, `completed_lifetime_value` should be 1103.00 and March completed revenue 279.00. I haven't run this, since I only have the logs and samples here.

## 3. Tests that would have caught it

The root problem is that `accepted_values` only checks that a status is known, not how it is classified. A mapping seed makes every new status fail until someone classifies it.

`seeds/raw_order_statuses.csv`:
```csv
status,is_completed
placed,false
shipped,false
delivered,true
completed,true
return_pending,false
returned,false
```

Then in `models/staging/schema.yml`, replace `accepted_values` on `stg_orders.status` with:
```yaml
          - relationships:
              arguments:
                to: ref('raw_order_statuses')
                field: status
```

Derive `is_completed` in `stg_orders` by joining this seed instead of using the hardcoded list. A new upstream status then fails the build until it is classified.

A reconciliation test against finance's number would also have flagged this. `tests/assert_completed_ltv_matches_orders.sql`:
```sql
select o.completed, c.completed_ltv
from (select sum(amount) as completed from {{ ref('orders') }} where is_completed) o
cross join (select sum(completed_lifetime_value) as completed_ltv from {{ ref('customers') }}) c
where abs(o.completed - c.completed_ltv) > 0.005
```

This one is a consistency check only. It would not have caught the original bug, because both models were wrong in the same way. Only the mapping seed, or a comparison against the admin's totals, catches that.
