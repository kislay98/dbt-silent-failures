claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 21 s | USD 0.115 | 2026-10-14 | project f769fdf | prompt 2b73e24d3b63

## 1. Root cause

**The `status` values in the raw orders data changed casing partway through, and `customers.sql` compares status case-sensitively.**

- **Data:** `seeds/raw_orders.csv` (loaded as `raw_orders`). January 2018 uses lowercase statuses (`completed`, `returned`, …). From February 2018 the feed switches to Title-case (`Completed`, `Placed`, `Shipped`, `Return_pending`). February even has both forms (14 `Completed` and 12 `completed`). `stg_orders.sql` passes `status` through untouched.
- **Code:** `models/customers.sql`, in `customer_payments`:
  ```sql
  sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
  ```
  This only matches lowercase `completed`, so the 31 `Completed` orders are dropped.
- **The numbers match exactly:** `completed` 604.00 + `Completed` 499.00 = **1103.00**, the finance ledger figure. `sum(amount)` is still 1672.00 because the total does not filter on status.
- **Why no test failed:** the `accepted_values` tests on `stg_orders.status` and `orders.status` (`models/staging/schema.yml`, `models/schema.yml`) were widened to accept both `'completed'` and `'Completed'`, and so on. That turned the test into a guard that hides the drift instead of catching it.

## 2. Fix

Normalize once, in staging, so every downstream model sees a single canonical value.

`models/staging/stg_orders.sql`:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        lower(trim(status)) as status

    from source

)
```

No change is needed in `customers.sql`. After the rebuild, `sum(completed_lifetime_value)` should be 1103.00. The `orders` grouped by status will collapse to `completed` 1103, `placed` 284, `shipped` 198, `return_pending` 38, `returned` 49.

Also raise the casing change with whoever owns the orders feed. Normalizing in staging protects the models, but it is worth knowing why the upstream format changed in February.

## 3. Tests that would have caught it

**a) Make `accepted_values` strict again** in both schema files, and leave out the capitalized variants:

```yaml
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Against the old staging model, this fails on `Completed`, `Placed`, `Shipped` and `Return_pending`. That is exactly the signal that got suppressed.

**b) Add a reconciliation test** that is independent of casing, so a future filter bug cannot hide behind a matching bug in the test. Create `tests/assert_completed_lifetime_value_reconciles.sql`:

```sql
-- completed revenue in customers must equal completed revenue in orders
with c as (
    select coalesce(sum(completed_lifetime_value), 0) as v from {{ ref('customers') }}
),
o as (
    select coalesce(sum(amount), 0) as v
    from {{ ref('orders') }}
    where lower(trim(status)) = 'completed'
)
select c.v as customers_completed, o.v as orders_completed
from c cross join o
where abs(c.v - o.v) > 0.01
```

Against the current build, this returns one row (604 vs 1103) and fails.

Two smaller things I noticed while reading:
- `schema.yml` documents a `total_order_amount` column on `customers` that does not exist. The real column is `customer_lifetime_value`.
- `raw_customer_regions` is described as "one row per customer" but has no `unique` test on `customer_id`. If that ever stops being true, the join in `region_revenue` will silently double-count revenue.
