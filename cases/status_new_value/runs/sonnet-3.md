claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 21 s | USD 0.060 | 2026-10-14 | project bef6b69

## 1. Root cause

Nothing is broken in the SQL joins or the amounts. The disagreement is a status definition that was never made explicit.

The admin counts `delivered` as completed. The models only count `status = 'completed'`.

The numbers reconcile exactly:

| | March | Jan–Mar |
|---|---|---|
| `orders` with `status='completed'` | 143.00 | 424 + 400 + 143 = **967.00** (matches `customers.completed_lifetime_value`) |
| `orders` with `status='delivered'` (March only) | 136.00 | 136.00 |
| Admin | 279.00 = 143 + 136 | 1103.00 = 967 + 136 |

Total revenue matches in every month because amounts and joins are right. Only the completed filter differs.

Where the filter lives:
- `models/customers.sql`, in `customer_payments`: `case when orders.status = 'completed'` is a hard-coded literal.
- `models/schema.yml` and `models/staging/schema.yml`: `accepted_values` lists `delivered` as valid, so the status passes every test. The tests check that the value is allowed, not what it means for revenue.
- The raw data has `delivered` only from March, so the upstream status vocabulary seems to have changed then. January and February are unaffected, which is why the gap appears only in March.

One caveat: I can't see the admin's definition or the `orders_status` doc block. The 136.00 match is exact, but please confirm with finance that `delivered` counts as completed.

## 2. Fix

Define "completed" once in staging and reuse it everywhere.

`models/staging/stg_orders.sql`:
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

`models/orders.sql`: add `orders.is_completed,` after `orders.status,` in `final`.

`models/customers.sql`: in `customer_payments`, replace the case expression with:
```sql
        sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

After a rebuild, March completed should be 279.00 and the Jan–Mar total 1103.00. Customer `completed_lifetime_value` should sum to 1103.00.

If finance may redefine this again, put the mapping in a seed (`order_status_map`: `status`, `is_completed`) and join to it instead of hard-coding the list.

## 3. Tests that would have caught it

**a) Reconcile against finance's control totals.** Load the admin's monthly completed totals as a seed, `finance_completed_revenue(month, amount)`, and add `tests/assert_completed_revenue_matches_finance.sql`:
```sql
select o.month, o.amount as model_amount, f.amount as finance_amount
from (
    select date_trunc('month', order_date) as month, sum(amount) as amount
    from {{ ref('orders') }}
    where is_completed
    group by 1
) o
join {{ ref('finance_completed_revenue') }} f using (month)
where abs(o.amount - f.amount) > 0.005
```
This test would have failed for March (143 against 279).

**b) Make every status be classified.** If you adopt the status-map seed, add a `relationships` test from `stg_orders.status` to `order_status_map.status`. A new status then fails the build until someone decides whether it counts as completed.

**c) Check that the models agree with each other.** Add a singular test that `sum(customers.completed_lifetime_value)` equals `sum(orders.amount) where is_completed`. This guards against `customers` and `orders` drifting apart again.
