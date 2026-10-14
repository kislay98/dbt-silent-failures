claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 44 s | USD 0.164 | 2026-10-14 | project f769fdf+wk6ext | prompt a91015185e01

## 1. Root cause

There are two problems in `raw_orders`. Both are new orders the models were never updated to handle, and every existing test was either unaffected or adjusted to let them through.

**A. A new status `delivered` is being counted as not completed.**
The source now sends `delivered` for orders that are finished. The admin counts them as completed, and the numbers match exactly:

| | `status = 'completed'` | + `delivered` | Admin |
|---|---|---|---|
| March | 143.00 | 143 + 136 = **279.00** | 279.00 |
| Since Jan | 424 + 400 + 143 = 967.00 | 967 + 65 + 136 = **1168.00** | 1168.00 |

`models/customers.sql` (`completed_amount`) only matches `status = 'completed'`, so these orders drop out of `completed_lifetime_value`. Customers 15 and 36, for example, show 0.00 completed. Nothing failed because `delivered` was added to the `accepted_values` lists in `models/staging/schema.yml` and `models/schema.yml`. That silenced the only test that would have flagged the new status, without updating the logic that depends on it.

**B. Back-dated orders 100–105 are missing from `daily_revenue`.**
Orders 100–105 arrived after order 99, which is dated 2018-04-09. They are dated in February: 02-03, 02-08, 02-13, 02-17, 02-22, 02-26. Together they are exactly the 6 orders and 65.00 by which `daily_revenue` falls short (480 − 415, 33 − 27). They also match the six mismatched dates in your diff query.

`models/daily_revenue.sql` is incremental with `delete+insert` on `order_date`, and it only rebuilds `order_date >= max(order_date) - 3 days`. Rows dated two months back are never picked up. `unique`/`not_null` on `daily_revenue` can't detect missing rows, so the build stays green.

## 2. Fix

**Normalize the status once, in staging** (`models/staging/stg_orders.sql`), so every downstream model keeps working:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- the source sends 'delivered' for finished orders since Feb 2018; the shop admin treats it as completed
        case when status = 'delivered' then 'completed' else status end as status

    from source

)
```

Then remove `'delivered'` from the `accepted_values` on `stg_orders.status` and `orders.status`. Keep it accepted at the source level instead (see the tests below), so the next new status fails loudly rather than being waved through.

**Make `daily_revenue` pick up late or back-dated orders** (`models/daily_revenue.sql`). Rebuild the recent window plus any date whose totals no longer match `orders`:

```sql
    {% if is_incremental() %}
    -- payments can land a day or two after the order, so the last three days are rebuilt on every run;
    -- orders can also be loaded late with an old order_date, so any date that no longer reconciles is rebuilt too
    where order_date >= (select max(order_date) - interval 3 day from {{ this }})
       or order_date in (
            select o.order_date
            from (select order_date, count(*) as orders, sum(amount) as revenue from orders group by 1) o
            left join {{ this }} d using (order_date)
            where d.order_date is null
               or d.orders <> o.orders
               or d.revenue is distinct from o.revenue
       )
    {% endif %}
```

At this size, simply changing it to `materialized='table'` is also a perfectly good fix.

Then rebuild everything once to repair the existing rows:

```
dbt build --full-refresh -s stg_orders+
```

After this, `daily_revenue` February should show 33 orders / 480.00. `customers.completed_lifetime_value` should sum to 1168.00, and March completed orders should total 279.00.

## 3. Tests that would have caught it

**Reconcile `daily_revenue` to `orders`.** This would have failed on the six February dates. Save it as `tests/daily_revenue_reconciles_to_orders.sql`:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
)

select o.order_date, o.orders, d.orders as dr_orders, o.revenue, d.revenue as dr_revenue
from o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where d.order_date is null
   or o.order_date is null
   or o.orders <> d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

**Guard the status vocabulary at the source.** Declare `raw_orders` in `schema.yml` with `accepted_values` on the *raw* status list. Then a new value like `delivered` fails the build until someone consciously maps it in `stg_orders`. Downstream, keep `stg_orders`/`orders` accepted values to the canonical set the models handle: `placed`, `shipped`, `completed`, `return_pending`, `returned`. With that in place, the original arrival of `delivered` would have failed instead of being added to the list.

Optionally, add a reconciliation test asserting `sum(customers.completed_lifetime_value) = sum(orders.amount) where status = 'completed'`. This guards against `customers` and `orders` drifting apart on their definition of "completed".
