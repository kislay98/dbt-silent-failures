claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 22 s | USD 0.087 | 2026-10-15 | project ad0b16b | prompt 1b2ae9e32b52

## 1. Root cause

The `delivered` status is the problem. `stg_orders.sql` passes `status` through unchanged, and `orders.sql` and `customers.sql` only treat the literal `'completed'` as completed. The numbers reconcile exactly:

| | Amount |
|---|---|
| Admin, since January | 1103.00 |
| `customers.completed_lifetime_value` (= `orders` with status `completed`) | 967.00 |
| Difference | **136.00** |
| `orders` with status `delivered` (8 orders) | **136.00** |

March has the same gap: 279.00 in the admin minus 143.00 in `orders` is 136.00. So all 8 `delivered` orders fall in March, and the admin counts them as completed.

The ingestion job started emitting `delivered` in March, where it previously wrote `completed`, and the models still treat it as a separate state. Total revenue is unaffected because it never looks at status.

Every test passes because both `accepted_values` lists (`models/schema.yml` and `models/staging/schema.yml`) include `delivered`. The tests check only that a status is known, not that it means what the downstream SQL assumes. Nothing reconciles the completed totals against anything.

I couldn't query the data, so I'm inferring from the arithmetic that `delivered` is the new completed state. Please confirm with finance and the ingestion owner that `delivered` is meant to count as completed. If it is a real intermediate step before completion, the admin definition is the one to change instead.

## 2. Fix

Normalize the status once in staging, so `orders`, `customers` and anything downstream agree. This goes in `models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- ingestion started sending 'delivered' in March 2018 for what used to be 'completed'
        case when status = 'delivered' then 'completed' else status end as status

    from source

)
```

Then remove `delivered` from both `accepted_values` lists, so a new status fails loudly instead of slipping through.

After `dbt build`, `customers.completed_lifetime_value` should sum to 1103.00 and March completed orders to 279.00. Rebuilding `customers` and `orders` is enough, since `daily_revenue` has no status column.

## 3. Tests that would have caught it

The first is a reconciliation test that computes completed revenue independently from the raw tables. It goes in `tests/assert_completed_revenue_reconciles.sql`:

```sql
with expected as (
    select sum(p.amount / 100) as amount
    from {{ ref('raw_payments') }} p
    join {{ ref('raw_orders') }} o on p.order_id = o.id
    where o.status in ('completed', 'delivered')
),
actual as (
    select sum(completed_lifetime_value) as amount from {{ ref('customers') }}
)
select *
from expected, actual
where abs(expected.amount - actual.amount) > 0.01
```

It should be paired with a second check in `tests/assert_completed_revenue_matches_admin.sql`, which compares monthly completed revenue in `orders` against the admin's monthly export loaded as a seed. That second check is the one that catches finance-versus-warehouse drift.

Also keep `accepted_values` on the raw status, with no `delivered` in the staging and model lists after the fix. A status the mapping doesn't know about then fails the build.
