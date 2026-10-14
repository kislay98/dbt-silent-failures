claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 25 s | USD 0.006 | 2026-10-15 | project ad0b16b | prompt 6cd8da3825cf

## 1. Root cause

The bad status casing enters in the seed and passes through staging unchanged, and the case-sensitive comparison in `customers.sql` drops the capitalized rows.

- **Data:** the `raw_orders` seed (not shown) has mixed-case `status` values. The orders breakdown shows `completed` (36 orders, 604.00) and `Completed` (31 orders, 499.00). `Placed`, `Shipped`, `Return_pending` and `Returned` have the same problem.
- **`models/staging/stg_orders.sql`:** `status` is selected as-is, so the mixed casing reaches every downstream model.
- **`models/customers.sql`:** `sum(case when orders.status = 'completed' then amount else 0 end)` is case-sensitive, so only the lowercase rows count. That gives 604.00, which is exactly the lowercase subtotal. Adding the capitalized `Completed` rows (499.00) gives 1103.00, the finance figure.
- **`models/staging/schema.yml` and `models/orders.sql`'s schema:** the `accepted_values` test lists both `'completed'` and `'Completed'` (and the other variants). The test therefore treats mixed casing as valid and passes. It enshrines the bad data rather than catching it.

`orders.amount` (1672.00) and `customer_lifetime_value` (1672.00) are correct because they don't filter on status. Only the status-filtered metric is wrong.

I haven't executed this. The conclusion comes from the status breakdown in the output sample (604 + 499 = 1103), which matches finance exactly.

## 2. Fix

Normalize status once, in staging, where source cleanup belongs.

`models/staging/stg_orders.sql`:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        lower(status) as status

    from source

)
```

Then make the accepted values lowercase only, in both `models/staging/schema.yml` and `models/schema.yml` (the `orders` model):
```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

`customers.sql` needs no change once status is normalized. After the rebuild, `sum(completed_lifetime_value)` should be 1103.00. `orders.status` will also come out lowercase. Nothing else in the project compares against the capitalized values.

I'd leave the seed alone. `raw_orders` should stay a faithful copy of the source, and the normalization belongs in staging.

## 3. Test that would have caught it

Two tests, either of which would have failed on this data:

**a) Tighten the existing `accepted_values` test** to canonical lowercase values only. The current list encodes the bug. On today's data, this test fails on `Completed`, `Placed`, etc., which forces the staging fix.

**b) Add a reconciliation test** that checks the metric against an independent calculation from raw data. Create `tests/assert_completed_lifetime_value_reconciles.sql`:
```sql
-- Returns rows (fails) if customers.completed_lifetime_value disagrees with
-- payments on orders whose status is 'completed' in any casing.
with expected as (

    select sum(p.amount / 100.0) as amount
    from {{ ref('raw_payments') }} as p
    join {{ ref('raw_orders') }} as o
        on p.order_id = o.id
    where lower(o.status) = 'completed'

),

actual as (

    select sum(completed_lifetime_value) as amount
    from {{ ref('customers') }}

)

select expected.amount as expected_amount, actual.amount as actual_amount
from expected
cross join actual
where abs(expected.amount - actual.amount) > 0.005
```

This test computes "completed" from raw data with `lower()`, so it doesn't share the model's logic. The current build would return a row (expected 1103.00, actual 604.00). The existing tests all check columns in isolation (`not_null`, `unique`, `accepted_values`), so none of them could detect a wrong metric.

Unrelated to this bug: `models/schema.yml` documents a `total_order_amount` column on `customers` that the model doesn't produce. It's worth cleaning up separately.
