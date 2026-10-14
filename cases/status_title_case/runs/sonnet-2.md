claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 18 s | USD 0.058 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

The status casing in `raw_orders` is inconsistent, and `customers.sql` compares it case-sensitively.

- `raw_orders.status` has capitalised values (`Completed`, `Placed`, `Shipped`, `Return_pending`) alongside the lowercase ones. The output sample shows them concentrated in Feb-Apr 2018, so it looks like a later load from a different source or format.
- `models/staging/stg_orders.sql` passes `status` through unchanged.
- `models/customers.sql` (`customer_payments` CTE) uses `case when orders.status = 'completed' then amount else 0 end`. That only matches lowercase `completed`, so every `Completed` order is counted in `customer_lifetime_value` but not in `completed_lifetime_value`.

The numbers reconcile exactly:

| status | amount |
|---|---|
| `completed` | 604.0 |
| `Completed` | 499.0 |
| total | **1103.0** (the ledger figure) |

`customer_lifetime_value` is still 1672.0 because it doesn't filter on status, which is why only the completed figure drifted.

The tests passed because the `accepted_values` lists in `models/schema.yml` and `models/staging/schema.yml` were widened to include the capitalised variants. That turned the guard into a rubber stamp for the bad data.

## 2. Fix

Normalise at the staging layer, so every downstream model sees one canonical casing.

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

Then restrict the `accepted_values` lists in both `schema.yml` files to the lowercase values only:
```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

After `dbt build`, `completed_lifetime_value` should sum to 1103.00. `orders.amount` and `customer_lifetime_value` stay at 1672.00, and `daily_revenue` is unaffected because it doesn't use status.

The seed data is untouched. If you would rather fix it at the source, lowercasing `status` in `seeds/raw_orders.csv` also works, but the staging fix keeps this from recurring on the next feed change.

## 3. Tests that would have caught it

**a) Strict `accepted_values`.** This is the lowercase-only list above. Against the raw data it fails on the `Completed`, `Placed`, `Shipped` and `Return_pending` rows if staging doesn't normalise them.

**b) Reconciliation test.** Add `tests/assert_completed_ltv_matches_orders.sql`:
```sql
-- completed revenue rolled up to customers must equal completed revenue on orders
with o as (
    select sum(amount) as amt from {{ ref('orders') }} where lower(status) = 'completed'
),
c as (
    select sum(completed_lifetime_value) as amt from {{ ref('customers') }}
)
select o.amt as orders_amt, c.amt as customers_amt
from o cross join c
where abs(o.amt - c.amt) > 0.005
```
Before the fix this returns a row (1103.0 vs 604.0), so the build fails. Using `lower(status)` on the `orders` side is what makes it independent of the bug.

If you want a cheap guard on staging as well, add a singular test that selects rows from `stg_orders` where `status <> lower(status)`.
