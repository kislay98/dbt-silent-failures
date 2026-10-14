claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 15 s | USD 0.053 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

`raw_orders` (the `seeds/raw_orders.csv` seed) has inconsistent `status` casing. Some rows are lowercase (`completed`) and others are title-case (`Completed`, `Placed`, `Shipped`, `Return_pending`). They look like they come from a different source or a later feed, since February onward mixes both.

Nothing normalizes the values:

- `models/staging/stg_orders.sql` passes `status` through untouched.
- `models/customers.sql` computes `completed_amount` with `case when orders.status = 'completed'`, which is a case-sensitive match. The 31 `Completed` orders worth 499.00 fall into the `else 0` branch.

The numbers reconcile exactly: 604 (`completed`) + 499 (`Completed`) = 1103, which is the finance figure. `sum(orders.amount)` and `customer_lifetime_value` are unaffected because they don't filter on status.

The tests pass because someone widened the `accepted_values` lists to include the capitalized variants in both `models/schema.yml` and `models/staging/schema.yml`. That turned the check that should have flagged the dirty data into one that accepts it.

## 2. Fix

Normalize at the staging layer so every downstream model sees one canonical value.

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

Tighten both `accepted_values` lists (`models/schema.yml` for `orders.status`, `models/staging/schema.yml` for `stg_orders.status`):
```yaml
values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Then run `dbt build`. `completed_lifetime_value` should sum to 1103.00. Optionally, also ask the source owner to fix the feed.

## 3. Tests that would have caught it

The strict `accepted_values` list above is the first one. It would have failed on the first `Completed` row instead of being widened to accommodate it.

Add a reconciliation test, `tests/assert_customers_completed_matches_orders.sql`:
```sql
-- completed revenue in customers must equal completed revenue in orders
with c as (select sum(completed_lifetime_value) as v from {{ ref('customers') }}),
     o as (select sum(amount) as v from {{ ref('orders') }} where status = 'completed')
select c.v as customers_total, o.v as orders_total
from c cross join o
where abs(coalesce(c.v, 0) - coalesce(o.v, 0)) > 0.005
```

This fails any time the two models disagree, whatever the cause. With the bug present it would have returned 604 vs 1103.
