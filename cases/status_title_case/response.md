## 1. Root cause

`raw_orders` (the `raw_orders` seed) has inconsistent casing in `status`. Some rows are `Completed`, `Placed`, `Shipped` and `Return_pending`, mostly for Feb and Mar 2018. Others are lowercase.

- `customers.sql` computes `completed_amount` with `case when orders.status = 'completed'`, which is case-sensitive in DuckDB. Only the lowercase `completed` orders (36 orders, 604.00) are counted. The 31 `Completed` orders (499.00) are silently counted as 0.
- 604 + 499 = 1103, which is exactly the finance ledger figure.
- `stg_orders.sql` passes `status` through without normalizing it.
- Both `accepted_values` tests (`models/schema.yml` and `models/staging/schema.yml`) have been widened to allow the capitalized variants (`'Placed'`, `'Completed'`, ...). That is why every test passes: the test was edited to accept the bad data instead of flagging it.
- `sum(orders.amount)` and `customer_lifetime_value` still equal 1672, because they don't filter on status.
- The same issue affects `Return_pending` vs `return_pending`, and any future status filter.

## 2. Fix

Normalize in staging, so every downstream model sees one canonical value.

`models/staging/stg_orders.sql`:
```sql
        cast(ordered_at as date) as order_date,
        lower(trim(status)) as status
```

Tighten both `accepted_values` lists (`models/schema.yml` for `orders` and `models/staging/schema.yml` for `stg_orders`) to the lowercase values only:
```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

After `dbt build`, `sum(completed_lifetime_value)` should be 1103.00, with `completed` at 535 orders... more precisely 67 orders and 1103.00 in the `orders` table. Also fix the seed CSV or the upstream feed if you own it.

## 3. Tests that would have caught it

**a. The strict `accepted_values` above.** On the raw data, it would have failed on `Completed`, which is the right signal. Put it on `stg_orders` and keep the casing fix in staging.

**b. A reconciliation test**, `tests/assert_completed_ltv_matches_orders.sql`. It is independent of the status spelling used in `customers.sql`:
```sql
with c as (
    select sum(completed_lifetime_value) as v from {{ ref('customers') }}
),
o as (
    select sum(amount) as v from {{ ref('orders') }}
    where lower(status) = 'completed'
)
select c.v as customers_total, o.v as orders_total
from c, o
where abs(coalesce(c.v, 0) - coalesce(o.v, 0)) > 0.005
```

This would have failed with 604 vs 1103 even with the loosened `accepted_values`. Because it compares against `lower(status)`, it stays valid after the staging fix as well.
