claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 49 s | USD 0.178 | 2026-10-14 | project f769fdf+wk6ext | prompt a91015185e01

The numbers point to two separate data problems. Both arrived through `raw_orders`, and the code's weak spots let them through. I checked everything below against your query output; I haven't run anything against the warehouse.

## 1. Root cause

### A. A new order status, `delivered`, isn't counted as completed

- **The new status:** The source system now writes `status = 'delivered'` for orders the shop admin counts as completed. Your output has 14 such orders:
  - February: 6 orders, 65.00
  - March: 8 orders, 136.00
- **The numbers reconcile exactly:**
  - Since January: 967.00 + 65.00 + 136.00 = **1168.00**
  - March: 143.00 + 136.00 = **279.00**
  - January has no `delivered` orders, which is why only February onward is off.
- **The code:** `models/customers.sql` only counts `status = 'completed'`. Finance's own filter on `orders` does the same.
- **Why no test failed:** `'delivered'` was added to the `accepted_values` list in both `models/staging/schema.yml` and `models/schema.yml`. That silenced the test that should have flagged the new value, but no model's logic was updated to handle it.

### B. Late-arriving February orders never reach `daily_revenue`

- **The late orders:** Orders 100–105 have ids higher than the April orders (98, 99) but `order_date`s in February. They were loaded into `raw_orders` long after February ended. Together they are exactly the gap Ops sees:
  - 6 orders, 12 + 9 + 15 + 6 + 20 + 3 = 65.00
  - 480 − 415 = 65, and 33 − 27 = 6
  - Your mismatch query lists exactly their six dates.
- **The code:** `models/daily_revenue.sql` is incremental and only rebuilds dates within 3 days of `max(order_date)`. Any order backfilled with an older date is silently skipped forever.
- **Why no test failed:** The model's tests only check `unique` and `not_null`, so nothing compares it to `orders`.

`orders` itself is correct, which is why total revenue matches the admin.

## 2. Fix

**`models/staging/stg_orders.sql`:** map `delivered` to `completed` once in staging, so every model downstream agrees with the admin.

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- the shop started emitting `delivered` in Feb 2018; the admin counts it as completed
        case status
            when 'delivered' then 'completed'
            else status
        end as status

    from source
```

**Both `schema.yml` files:** remove `'delivered'` from the `accepted_values` lists. That way the next new status fails the build instead of passing quietly.

**`models/daily_revenue.sql`:** `raw_orders` has no load timestamp, so there's no reliable way to know which old dates got new rows. The table is tiny, so rebuild it in full on every run:

```sql
{{ config(materialized='table') }}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

If it has to stay incremental, keep it that way but schedule a regular `--full-refresh`. Even then, gaps can open between refreshes.

**To repair the current data now:**

```
dbt build --full-refresh --select stg_orders+
```

## 3. Tests that would have caught this

1. **A strict `accepted_values` list on `stg_orders.status`:** use the original five values without `delivered`. The day the source introduced the new status, this would have failed.

2. **A reconciliation test between `daily_revenue` and `orders`**, saved as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with from_orders as (

    select order_date, count(*) as orders, round(sum(amount), 2) as revenue
    from {{ ref('orders') }}
    group by order_date

),

from_daily as (

    select order_date, orders, round(revenue, 2) as revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily_revenue

from from_orders o
full outer join from_daily d
    on o.order_date = d.order_date

where o.orders is distinct from d.orders
   or o.revenue is distinct from d.revenue
```

On today's data this test returns the six February dates.

Unrelated to these complaints, two other joins could inflate totals, and nothing tests for it:
- `raw_customer_regions` has an `updated_at` column, which suggests it may hold several rows per customer. That would multiply rows in `region_revenue`.
- `raw_payment_methods` has no uniqueness check, so a duplicate method row would multiply rows in `orders`.

Adding a `unique` test on `raw_customer_regions.customer_id` and on `raw_payment_methods.payment_method` would guard against both.
