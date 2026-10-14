claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 19 s | USD 0.004 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

The bug is a case mismatch in the `status` column, which is passed through unnormalized in `jaffle_shop/models/staging/stg_orders.sql`:

```sql
status
```

The raw `raw_orders.status` column contains two spellings of the same status. From the output sample:

| status | orders | amount |
|---|---|---|
| `completed` | 36 | 604.0 |
| `Completed` | 31 | 499.0 |

`customers.sql` filters with a case-sensitive comparison:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

So `completed_lifetime_value` only picks up the lowercase rows. The 604.00 is exactly the lowercase subtotal, and the finance number is the sum of both spellings: 604 + 499 = 1103. The same mismatch affects the `Return_pending`/`return_pending` pair, though that doesn't affect the completed figure.

`orders.amount` is correct (1672), since it doesn't filter on status. That matches finance's total once the 569 non-completed amount is added back: 1103 + 569 = 1672.

**Why the tests didn't catch it:** the `accepted_values` tests in both `models/schema.yml` (orders) and `models/staging/schema.yml` (stg_orders) list the mixed-case values explicitly:

```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned', 'Placed', 'Shipped', 'Completed', 'Return_pending', 'Returned']
```

The test was written to allow the bad data, so it passed. The schema description for `completed_lifetime_value` ("orders with status completed") shows the intended behavior, which the data doesn't match.

## 2. Fix

Normalize the status in staging, where the raw values enter the project:

`jaffle_shop/models/staging/stg_orders.sql`:
```sql
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        lower(trim(status)) as status
```

The `trim` is defensive. The sample shows no whitespace variants, so you can drop it if you prefer.

Then remove the mixed-case values from both `accepted_values` lists (`models/staging/schema.yml` and `models/schema.yml`), so they only allow the canonical lowercase set:

```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Rebuild the affected models. `daily_revenue` is incremental, but its amounts don't change, so `--full-refresh` isn't needed:

```
dbt build --select stg_orders+
```

Expected result after the rebuild: `sum(completed_lifetime_value)` on `customers` should be 1103.00. I derived this from the output you provided and haven't re-run the build, so confirm it with the query.

## 3. Test that would have caught it

The primary test is the strict `accepted_values` list above. Once the mixed-case values are removed, the current data fails the test on the `Completed` and `Return_pending` rows, which is the failure you want.

For a guard against the same class of bug in the finance number itself, add a singular test that reconciles the output against an independent calculation. Save it as `tests/assert_completed_revenue_matches_orders.sql`:

```sql
with expected as (
    select sum(amount) as total
    from {{ ref('orders') }}
    where lower(status) = 'completed'
),

actual as (
    select sum(completed_lifetime_value) as total
    from {{ ref('customers') }}
)

select expected.total, actual.total
from expected
cross join actual
where abs(coalesce(expected.total, 0) - coalesce(actual.total, 0)) > 0.01
```

This fails against the current data (604 vs. 1103) and passes after the fix. It is a sanity check on the model logic, not an external reconciliation, so it won't catch a wrong mapping at the source. For that, compare against the finance ledger's total for the same period.
