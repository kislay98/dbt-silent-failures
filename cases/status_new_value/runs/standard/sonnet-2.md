claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 23 s | USD 0.091 | 2026-10-15 | project ad0b16b | prompt 1b2ae9e32b52

## 1. Root cause

The `delivered` status is never treated as completed. Every "completed" figure in the project is built from `status = 'completed'`, but the upstream shop system now emits `delivered` for some orders, and the admin counts those as completed.

The numbers confirm it:

- `orders` by status shows `delivered`: 8 orders, **136.00**.
- 1103 (admin since Jan) − 967 (`completed` in `orders`) = **136**.
- 279 (admin, March) − 143 (`completed`, March) = **136**.

So all 8 `delivered` orders fall in March and account for the whole gap. I inferred the March placement from the arithmetic rather than querying the dates. Total revenue matches because it doesn't filter on status.

The places involved:

- `models/staging/stg_orders.sql` passes the raw `status` through untouched, so `delivered` reaches every downstream model.
- `models/customers.sql`, in the `customer_payments` CTE, computes `completed_amount` with `case when orders.status = 'completed'`, which drops `delivered`.
- `models/schema.yml` and `models/staging/schema.yml` have `delivered` in the `accepted_values` lists for `status`. It was added there to make the test pass, but nothing maps it to a meaning. That is why all 35 checks passed while the numbers were wrong.

## 2. Fix

Normalize the status in staging, so every model agrees on the meaning of completed. This keeps the original value as `raw_status`:

```sql
-- models/staging/stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status as raw_status,
        -- the shop system now reports delivered orders as `delivered`; finance treats them as completed
        case when status = 'delivered' then 'completed' else status end as status

    from source

)
```

Then remove `'delivered'` from the `accepted_values` lists in both `schema.yml` files, so the tests check the normalized statuses. A new unmapped status will then fail the build instead of being silently accepted.

After `dbt build`, the March completed total should be 279.00 and the lifetime total 1103.00 in both `orders` and `customers.completed_lifetime_value`.

`daily_revenue` doesn't use status, so it is unaffected and needs no full refresh.

## 3. Tests that would have caught it

A singular test that reconciles completed revenue straight from the raw tables, using the finance definition of completed. It fails today with 967 vs 1103:

```sql
-- tests/assert_completed_revenue_reconciles_to_raw.sql
with expected as (

    select sum(p.amount) / 100.0 as amount
    from {{ ref('raw_orders') }} o
    join {{ ref('raw_payments') }} p on p.order_id = o.id
    where o.status in ('completed', 'delivered')

),

actual as (

    select
        (select sum(amount) from {{ ref('orders') }} where status = 'completed') as orders_amount,
        (select sum(completed_lifetime_value) from {{ ref('customers') }}) as customers_amount

)

select *
from expected
cross join actual
where abs(expected.amount - actual.orders_amount) > 0.005
   or abs(expected.amount - actual.customers_amount) > 0.005
```

To prevent a recurrence, add a source-side guard: `accepted_values` on the raw status, with `severity: error` and no edits just to make it pass, or a status-to-category mapping seed with a `relationships` test. Either way, a new upstream status then forces someone to decide whether it counts as completed.
