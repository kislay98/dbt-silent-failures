claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 15 s | USD 0.085 | 2026-10-15 | project ad0b16b | prompt 6cd8da3825cf

## 1. Root cause

The status values in `seeds/raw_orders.csv` are inconsistently cased. Roughly half the orders carry capitalized statuses (`Completed`, `Placed`, `Shipped`, `Return_pending`) and the rest are lowercase. This is visible in the `orders` output: `completed` has 36 orders and 604.0, while `Completed` has 31 orders and 499.0.

`models/staging/stg_orders.sql` passes `status` through unchanged. `models/customers.sql` then filters case-sensitively:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

Only the lowercase rows match, so you get 604.00. The capitalized `Completed` rows (499.00) are silently counted as 0. That accounts for the gap: 604 + 499 = 1103, which is the finance ledger figure. `sum(orders.amount)` is unaffected because it doesn't filter on status.

Every test passes because the `accepted_values` tests in `models/schema.yml` (on `orders.status`) and `models/staging/schema.yml` (on `stg_orders.status`) explicitly list both the lowercase and the capitalized variants. That whitelist accepts the dirty data instead of flagging it.

## 2. Fix

Normalize the status once, in staging, so all downstream models see one canonical value.

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

Then tighten both `accepted_values` lists (`models/staging/schema.yml` and `models/schema.yml`) to lowercase only:
```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

After `dbt build`, `sum(completed_lifetime_value)` should be 1103.00. `return_pending` orders and the other statuses will also each collapse into a single bucket. `sum(orders.amount)` stays at 1672.00. `daily_revenue` and `region_revenue` don't use status, so they are unaffected.

Also ask whoever owns the feed why casing changed. If it can recur, the `lower()` in staging covers it, and the strict test will catch any new, genuinely unknown status values.

## 3. Tests that would have caught it

**a) The strict `accepted_values` test.** With the lowercase-only list, it fails on the raw `Completed`, `Placed`, `Shipped` and `Return_pending` values. This is the most direct catch, and it is what the widened whitelist defeated. If you keep a test on the raw source, put it on `raw_orders` rather than the staged model.

**b) A reconciliation test** that checks the customer rollup against orders, independent of how the status is spelled. Save it as `tests/assert_customers_completed_ltv_matches_orders.sql`:
```sql
-- completed_lifetime_value must equal completed order revenue, regardless of status casing
with expected as (
    select sum(amount) as amount
    from {{ ref('orders') }}
    where lower(status) = 'completed'
),
actual as (
    select sum(completed_lifetime_value) as amount
    from {{ ref('customers') }}
)
select expected.amount as expected_amount, actual.amount as actual_amount
from expected, actual
where abs(expected.amount - actual.amount) > 0.01
```

Before the fix, this returns a row with 1103.0 vs 604.0, so the build fails. It also guards against any future status-handling drift in `customers.sql`.
