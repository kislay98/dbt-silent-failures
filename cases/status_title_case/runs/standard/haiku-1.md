claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 21 s | USD 0.005 | 2026-10-15 | project ad0b16b | prompt 6cd8da3825cf

## 1. Root cause

The raw `orders` data has mixed-case status values, and nothing normalizes them before the marts compare against `'completed'`.

- `raw_orders` contains both `completed` (36 orders, $604.00) and `Completed` (31 orders, $499.00).
- `stg_orders.sql` passes `status` through unchanged.
- `customers.sql` computes `completed_lifetime_value` with `case when orders.status = 'completed'`. That comparison is case-sensitive, so every `Completed` order is treated as not completed.

The numbers reconcile exactly: $604.00 (`completed`) + $499.00 (`Completed`) = **$1,103.00**, the finance ledger figure. `sum(orders.amount)` is still $1,672.00 because `orders.amount` doesn't depend on status.

The tests passed because `schema.yml` (both the root and `staging/` versions) lists the capitalized values in `accepted_values`. The `accepted_values` test was written to accept the dirty data instead of flagging it, so the passing test is a symptom of the bug. The `daily_revenue` and `region_revenue` models don't filter on status, which is why they're unaffected.

## 2. Fix

Normalize status once, in staging, so every downstream model gets the same values.

**`models/staging/stg_orders.sql`**: lowercase the status in the `renamed` CTE.

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

**`models/staging/schema.yml`** and **`models/schema.yml`**: drop the capitalized variants from the `accepted_values` lists so the test enforces the normalized set:

```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

`customers.sql` needs no change because its `orders` CTE reads from `stg_orders`. After `dbt build`, `sum(completed_lifetime_value)` should be 1103.00 and `sum(orders.amount)` should remain 1672.00. I haven't run this, so confirm after the build.

Unrelated doc drift I noticed: `models/schema.yml` documents `customers.total_order_amount`, but `customers` has no such column. Remove that entry or add the column.

## 3. Test that would have caught it

Add a singular test that reconciles the mart against the raw source with an independent, case-insensitive filter. It fails whenever the marts and raw data disagree on completed revenue.

**`tests/assert_completed_revenue_matches_raw.sql`**

```sql
with expected as (

    select sum(p.amount / 100.0) as amount
    from {{ ref('raw_orders') }} as o
    join {{ ref('raw_payments') }} as p
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
where abs(expected.amount - actual.amount) > 0.001
```

Before the fix, this returns one row (expected 1103.00, actual 604.00). After the fix, it returns no rows. The lowercase `accepted_values` change is the other guard: it fails on any capitalized status that reaches staging.
