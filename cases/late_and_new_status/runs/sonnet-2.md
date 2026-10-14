claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 37 s | USD 0.088 | 2026-10-14 | project f769fdf+wk6ext | prompt c5114118dec9

## 1. Root cause

There are two independent problems, and both involve the six extra February orders (ids 100-105) and the `delivered` status.

**A. `delivered` is never counted as completed (`models/customers.sql`, `models/staging/stg_orders.sql`, both `schema.yml`)**

The numbers reconcile exactly with the admin:

| Source | Completed amount |
|---|---|
| `orders` with `status = 'completed'` (Jan 424 + Feb 400 + Mar 143) | 967 |
| `delivered` in February | 65 |
| `delivered` in March | 136 |
| Total, matching the admin | 1168 |

March in the admin is 143 + 136 = 279.

The source now emits a `delivered` status, which the admin treats as completed. Someone added `delivered` to the `accepted_values` lists in both `schema.yml` files to make the tests pass. Nobody mapped it into the completed logic. `customers.sql` hardcodes `orders.status = 'completed'`, so every delivered order is dropped from `completed_lifetime_value`.

**B. `daily_revenue` never picks up late-arriving orders (`models/daily_revenue.sql`)**

The incremental filter is `order_date >= max(order_date) - 3 days`. The table's max date is in April, so only April is rebuilt. Orders 100-105 are dated 2018-02-03 to 2018-02-26 and arrived after those days were already built. They are never selected, so Feb shows 27 orders and 415.00 instead of 33 and 480.00. The mismatch query lists exactly those six dates. The comment about payments landing a day or two late assumes that late data has a recent `order_date`, which isn't true here.

Why the tests passed: `unique`, `not_null` and `accepted_values` only check shape. Nothing reconciles `daily_revenue` with `orders`, and nothing forces a new status to be classified.

## 2. Fix

**`stg_orders.sql`**: add an explicit classification. A status that isn't listed gets `null`, which the new test catches.

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status,
        case
            when status in ('completed', 'delivered') then true
            when status in ('placed', 'shipped', 'return_pending', 'returned') then false
        end as is_completed

    from source
```

**`customers.sql`**: the `customer_orders` CTE is unchanged. Update the `customer_payments` CTE:

```sql
        sum(case when orders.is_completed then amount else 0 end) as completed_amount
```

**`orders.sql`**: add `orders.is_completed,` to the `final` select so downstream users can use it.

**`daily_revenue.sql`**: the simplest correct fix is to drop the incremental logic. It's a small aggregate over `orders`, and the lookback can't be made safe without a load timestamp.

```sql
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

The project default is `+materialized: table`, so no config block is needed. If you keep it incremental, add a `loaded_at` column to the raw tables and filter on that instead of `order_date`.

Either way, run a one-time rebuild so the stale February rows are replaced:

```
dbt run --full-refresh -s daily_revenue customers orders
```

Please confirm with finance and the source owner that `delivered` should count as completed. It reconciles exactly to the admin, but the status definition is theirs.

After the fix, expect `completed_lifetime_value` to sum to 1168.00 and February in `daily_revenue` to show 33 orders and 480.00.

## 3. Tests

**`tests/assert_daily_revenue_matches_orders.sql`** would have caught problem B:

```sql
with o as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select coalesce(o.order_date, d.order_date) as order_date
from o
full outer join {{ ref('daily_revenue') }} as d
    on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

**`models/staging/schema.yml`** would have caught problem A. Any new status added to `accepted_values` now fails here until someone decides whether it counts as completed:

```yaml
      - name: is_completed
        tests:
          - not_null
```
