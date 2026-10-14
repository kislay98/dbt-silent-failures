claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 21 s | USD 0.004 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

Status values in `raw_orders` are inconsistently cased, and `stg_orders.sql` passes them through unchanged:

- `completed` (lowercase): 36 orders, 604.00
- `Completed` (capitalized): 31 orders, 499.00
- 604.00 + 499.00 = **1103.00**, which matches the finance ledger exactly.

`customers.sql` computes `completed_lifetime_value` with `case when orders.status = 'completed'`. That comparison is case-sensitive in DuckDB, so the 31 `Completed` orders fall into the `else 0` branch. The customers table therefore sums only 604.00. `lifetime_value` (1672.00) is correct because it doesn't filter on status.

The tests let this through because `models/schema.yml` and `staging/schema.yml` both list the capitalized variants (`'Placed'`, `'Completed'`, `'Return_pending'`, …) in the `accepted_values` test. The test is asserting that the dirty values are valid, so it passes.

The bug is in `jaffle_shop/models/staging/stg_orders.sql`, with the permissive status lists in `jaffle_shop/models/staging/schema.yml` and `jaffle_shop/models/schema.yml` hiding it. `customers.sql` is correct for normalized input.

## 2. Fix

Normalize status once, in staging, so every downstream model gets consistent values:

```sql
-- jaffle_shop/models/staging/stg_orders.sql
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

Then tighten the `accepted_values` lists to lowercase only, in both `jaffle_shop/models/staging/schema.yml` (stg_orders.status) and `jaffle_shop/models/schema.yml` (orders.status):

```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Rebuild the affected models:

```
dbt build --select stg_orders+
```

`daily_revenue` is also downstream of `orders`, but it doesn't use status and `amount` is unchanged, so its values stay the same. Expected result after the rebuild: `sum(completed_lifetime_value)` = 1103.00 and `sum(customer_lifetime_value)` = 1672.00.

I haven't run this. The numbers above come from the sample output and the ledger figure you gave.

Separately, the source data should be checked. If the upstream system is emitting mixed case, it's worth raising with its owners, but the staging fix is correct regardless.

## 3. Test that would have caught it

**Reconciliation test.** This compares the customers output against an independent calculation from orders, and it would have failed (1103 expected vs. 604 actual). It has to use `lower(status)` on the expected side. Filtering on `status = 'completed'` in the test would reproduce the same bug and pass.

```sql
-- jaffle_shop/tests/assert_completed_revenue_reconciles.sql
with expected as (

    select sum(amount) as total
    from {{ ref('orders') }}
    where lower(status) = 'completed'

),

actual as (

    select sum(completed_lifetime_value) as total
    from {{ ref('customers') }}

)

select expected.total as expected_total, actual.total as actual_total
from expected
cross join actual
where abs(expected.total - actual.total) > 0.005
```

**Guard against casing drift.** With the lowercase-only `accepted_values` list, this test fails on any uppercase status. A singular test makes the rule explicit:

```sql
-- jaffle_shop/tests/assert_order_status_is_lowercase.sql
select order_id, status
from {{ ref('stg_orders') }}
where status != lower(status)
```

Both tests return rows only when something is wrong, so `dbt build` fails when either condition is violated.

Unrelated, but worth fixing while you're in there: `models/schema.yml` documents a `total_order_amount` column on `customers` that the model doesn't produce. The real column is `customer_lifetime_value`.
