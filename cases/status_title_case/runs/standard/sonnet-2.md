claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 16 s | USD 0.085 | 2026-10-15 | project ad0b16b | prompt 6cd8da3825cf

## 1. Root cause

`raw_orders` (the `status` column, passed through unchanged by `models/staging/stg_orders.sql`) has mixed-case statuses. Some rows are `completed` and others are `Completed`. The same happens with `Placed`, `Shipped` and `Return_pending`.

`models/customers.sql` then uses a case-sensitive comparison in `customer_payments`:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

That only matches the lowercase rows. The `orders` status breakdown shows this:

| status | orders | amount |
|---|---|---|
| `completed` | 36 | 604.0 |
| `Completed` | 31 | 499.0 |

604.00 is exactly the lowercase bucket, and 604 + 499 = **1103.00**, which is what finance has. The other totals are right because they don't filter on status: `sum(orders.amount)` is 1672, and `customer_lifetime_value`, `daily_revenue` and `region_revenue` all reconcile to 1672.

The tests pass because someone widened `accepted_values` in both `models/schema.yml` (`orders.status`) and `models/staging/schema.yml` (`stg_orders.status`) to include `'Placed', 'Shipped', 'Completed', ...`. That turned a test that would have caught the drift into one that blesses it.

## 2. Fix

Normalize the status once, in staging. `models/staging/stg_orders.sql`:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        lower(trim(status)) as status

    from source
```

Then restore strict lowercase values in both `schema.yml` files:

```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

After `dbt build`, `sum(customers.completed_lifetime_value)` should be 1103.00. `customer_lifetime_value` stays at 1672.00.

Separately, the `customers` schema documents `total_order_amount`, but the model outputs `customer_lifetime_value`. That column is undocumented and untested, so it's worth fixing in the same change.

## 3. Tests that would have caught it

The strict `accepted_values` above is the first line of defense. It fails on `Completed`, so the case problem is flagged at the source instead of being tolerated.

Add a singular test that reconciles the customer rollup to the order grain, `tests/assert_customers_completed_ltv_matches_orders.sql`:

```sql
-- completed revenue in customers must equal completed revenue in orders (case-insensitive on purpose)
with o as (
    select sum(amount) as completed from {{ ref('orders') }}
    where lower(status) = 'completed'
),
c as (
    select sum(completed_lifetime_value) as completed from {{ ref('customers') }}
)
select o.completed as orders_completed, c.completed as customers_completed
from o cross join c
where abs(o.completed - c.completed) > 0.005
```

Before the fix this returns a row (1103.0 vs 604.0), so the build would have failed. It compares against `lower(status)` rather than relying on the cleaned column, so it still catches the problem if the staging normalization is ever removed.
