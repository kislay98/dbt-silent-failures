claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 40 s | USD 0.006 | 2026-10-14 | project bef6b69 | prompt f5793c9fa6c4

## Summary

I haven't run anything against the warehouse. The findings below come from the output you pasted, and the numbers reconcile.

## 1. Root cause

**Data:** `raw_payments.amount` is NULL for some payments, for example ids 15, 30, 62 and 67 in the sample. The column is INTEGER, so this isn't a type-cast loss. The ingestion job wrote NULL where a settled amount should be. For order 25, payment 30 (coupon) is NULL. The settlement difference of 58.00 − 42.00 = 16.00 suggests that payment should be 16.00.

**`models/staging/stg_payments.sql`** passes the NULL through: `amount / 100` on NULL is NULL. Nothing rejects it.

**`models/orders.sql`** is where the money goes missing. `sum(amount)` in `order_payments` silently skips NULLs, so order 25 becomes 20 + 22 = 42.00. Summed over Jan–Mar, `orders.amount` is 458 + 394 + 616 = 1468.00.

**`models/daily_revenue.sql`** explains why the dashboard still matches the settlement. It is incremental with `delete+insert` and rebuilds only the last three days. Jan–Mar are older than that window, so they keep the values from the run before the NULLs arrived. Its Jan–Mar sum is 496 + 415 + 622 = 1533.00, which equals the settlement. The dashboard isn't right; it is frozen. The comment about late-arriving payments doesn't cover corrections to old data.

**Why the tests passed:**
- No `not_null` test exists on `stg_payments.amount`. This is the test that would have caught it.
- `orders.amount` has `not_null`, but it still passes because `sum()` returns a value whenever an order has at least one non-NULL payment.
- Nothing compares `daily_revenue` with `orders`.
- The log shows 27 of the 29 declared data tests. The two `raw_customer_regions` seed tests never ran because the seeds aren't in the build. That is a separate gap, but it's worth fixing.

## 2. Fix

**Data (required):** Have the ingestion job reload `raw_payments` from the processor, and confirm the true cents for every NULL row. Don't coalesce NULL to 0 in SQL, because that would reproduce the same bug quietly.

**`models/daily_revenue.sql`:** `orders` is already a full table, so rebuilding `daily_revenue` as a table is cheap and removes the stale-history problem:

```sql
{{
    config(
        materialized='table'
    )
}}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

Then run `dbt run --select daily_revenue` after the raw data is fixed.

## 3. Tests that would have caught it

**Add a `not_null` test to the payment amount** in `models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: payment_id
        tests:
          - unique
          - not_null
      - name: amount
        tests:
          - not_null
      - name: payment_method
        tests:
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

This fails the build on the first night a NULL appears.

**Add a reconciliation test** at `tests/assert_daily_revenue_matches_orders.sql`. This catches the stale incremental rows, which the existing tests can't see:

```sql
select
    d.order_date,
    d.revenue,
    o.amount
from {{ ref('daily_revenue') }} as d
join (
    select order_date, sum(amount) as amount
    from {{ ref('orders') }}
    group by order_date
) as o
    on d.order_date = o.order_date
where abs(d.revenue - o.amount) > 0.005
```

A singular test returns rows only on failure, so an empty result means it passed.

**Next steps you may want:**
- Run the seeds, or otherwise get the `raw_customer_regions` tests into the build.
- `region_revenue` joins on `raw_customer_regions`, which has an `updated_at` column. If a customer has more than one row there, revenue will double-count. The seed is documented as one row per customer, so this needs checking.
- `schema.yml` documents `total_order_amount` on `customers`, which the model doesn't produce.
