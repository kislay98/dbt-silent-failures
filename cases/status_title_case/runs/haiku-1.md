claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 19 s | USD 0.004 | 2026-10-22

## 1. Root cause

The bug is a case mismatch in the raw order data, and the `customers` model is where it becomes wrong revenue.

`raw_orders.status` contains two casings of the same status. Via `stg_orders` (which passes `status` through unchanged) and `orders`, the warehouse holds:

| status | orders | amount |
|---|---|---|
| `completed` | 36 | 604.0 |
| `Completed` | 31 | 499.0 |

`jaffle_shop/models/customers.sql`, in `customer_payments`, filters with a case-sensitive comparison:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

That drops the 31 `Completed` orders (499.00), so `completed_lifetime_value` sums to 604.00 instead of 1103.00 (604 + 499 = 1103, which matches finance exactly). `total_amount` is unaffected, which is why `customer_lifetime_value` still sums to 1672.00.

The tests let this through. `models/schema.yml` (orders) and `models/staging/schema.yml` (stg_orders) both have `accepted_values` lists that include both `'completed'` and `'Completed'`, so the mixed-case values pass. The test accepts the dirty data instead of flagging it, so the build reports 40/40 PASS.

Secondary: `return_pending`/`Return_pending` has the same problem, but it does not affect this metric.

## 2. Fix

Normalize status once in staging, so every downstream model sees one canonical value:

`jaffle_shop/models/staging/stg_orders.sql`:

```sql
select
    id as order_id,
    user_id as customer_id,
    ordered_at,
    cast(ordered_at as date) as order_date,
    lower(trim(status)) as status

from source
```

`customers.sql` and `orders.sql` need no changes. After the rebuild, `completed` should show 67 orders and 1103.00, and `customers.completed_lifetime_value` should sum to 1103.00.

Also tighten the accepted values to the canonical lowercase set in both `models/schema.yml` and `models/staging/schema.yml`:

```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

The `accepted_values` test on `stg_orders` will then fail on the raw data until the normalization above is in place, which is the intended behavior. The `Placed`/`Shipped`/`Completed` entries should be removed from the list so the test isn't blessing the bad casing.

Optionally, fix the source too: if the `raw_orders` seed was typed by hand or exported from a system that writes mixed case, correct the CSV. The `lower(trim())` in staging is the defensive fix, since the seed is reloaded on every build and the source may keep producing mixed case.

One thing to check before merging: any downstream consumer (BI dashboards, ad hoc queries) that filters on `'Completed'` will see those rows move to `'completed'`.

## 3. A test that would have caught it

Add a singular test that reconciles the customer-level metric against the order-level data. It fails on the current build (1103.00 expected vs. 604.00 actual):

`jaffle_shop/tests/assert_completed_revenue_reconciles.sql`:

```sql
with completed_orders as (

    select coalesce(sum(amount), 0) as amount
    from {{ ref('orders') }}
    where lower(status) = 'completed'

),

customer_completed as (

    select coalesce(sum(completed_lifetime_value), 0) as amount
    from {{ ref('customers') }}

)

select
    completed_orders.amount as expected,
    customer_completed.amount as actual
from completed_orders
cross join customer_completed
where abs(completed_orders.amount - customer_completed.amount) > 0.001
```

It doesn't depend on the casing rules, so it stays valid if someone adds another status variant later. Note that it checks `lower(status)` on the reconciliation side, so it would also pass after the staging fix, which is what you want. The stricter accepted-values change in the staging schema is what would have failed loudly at the source.

I worked from the logs and output you pasted and did not run anything against the project. If you want, I can apply the staging change and the new test and run `dbt build` to confirm the numbers.
