claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 35 s | USD 0.084 | 2026-10-14 | project f769fdf+wk6ext | prompt c5114118dec9

There are two independent bugs. I haven't run anything, since I only have the pasted files and output. The diagnosis comes from reconciling the numbers.

## 1. Root cause

**A. `models/customers.sql` (`completed_amount`) and the finance definition of "completed".**
`customers.sql` hardcodes `orders.status = 'completed'`. The source now also emits a `delivered` status, and finance counts it as completed. The gap matches to the cent:

| Period | Admin | `status = 'completed'` | `delivered` orders | Difference explained |
|---|---|---|---|---|
| March | 279.00 | 143.00 | 136.00 | 143 + 136 = 279 ✓ |
| Since January | 1168.00 | 967.00 | 65 (Feb) + 136 (Mar) = 201 | 967 + 201 = 1168 ✓ |

`delivered` was added to the `accepted_values` lists in both `models/schema.yml` and `models/staging/schema.yml`. That made the new status pass the tests without anyone deciding how it should be classified.

**B. `models/daily_revenue.sql` (the incremental filter).**
The filter is `order_date >= max(order_date) - 3 days`, and `max(order_date)` is in April. Orders 100–105 have February dates but IDs higher than the April orders 98 and 99, so they arrived late. They fall outside the 3-day window, so they were never inserted.

- The six missing orders are exactly the dates in your diff query, which gives 33 − 27 = 6 orders in February.
- They are all `delivered`, totalling 12 + 9 + 15 + 6 + 20 + 3 = 65, which gives 480 − 415 = 65.
- The comment says payments land late, but the filter is on `order_date`, not on load time, so it only protects against late payments on recent orders. Late-arriving orders with old dates are never picked up.
- This is why the two used to match: before orders 100–105 arrived, there was nothing to miss.

## 2. Fix

**A. Define completion once, in `stg_orders.sql`:**
```sql
        status,
        status in ('completed', 'delivered') as is_completed
```
**In `customers.sql`:**
```sql
        sum(case when orders.is_completed then amount else 0 end) as completed_amount
```
Please confirm with finance that `delivered` counts as completed. The numbers say it does, but it is a business rule.

**B. `daily_revenue.sql`.** This is a tiny aggregate and there is no load timestamp to use as a watermark, so the simplest correct fix is to drop the incremental config and let the project default (`table`) apply:
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
If it must stay incremental, you need a load timestamp from the ingestion job to filter on. Either way, repair the existing table now with:
```
dbt build --full-refresh -s daily_revenue+
```

**Expected results after the fix:** `daily_revenue` February comes to 33 orders and 480.00; March completed comes to 279.00; the `customers.completed_lifetime_value` total comes to 1168.00.

## 3. Tests

**Reconciliation, `tests/assert_daily_revenue_matches_orders.sql`.** This would have caught B on the first run after the late orders landed:
```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as d_orders, o.revenue, d.revenue as d_revenue
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

**Completed-value reconciliation, `tests/assert_completed_value_matches_orders.sql`.** It checks the customers rollup against `orders` using the shared flag:
```sql
select 1
from (
    select
        (select round(sum(completed_lifetime_value), 2) from {{ ref('customers') }}) as c,
        (select round(sum(o.amount), 2)
         from {{ ref('orders') }} o
         join {{ ref('stg_orders') }} s using (order_id)
         where s.is_completed) as o
)
where c is distinct from o
```

**Status classification (the test for A).** Widening `accepted_values` hid the new status, so a status list alone cannot catch this. Add a seed `raw_order_statuses` (`status`, `is_completed`) that finance owns, and a `relationships` test from `stg_orders.status` to it. Derive `is_completed` from that seed instead of the hardcoded list. A new upstream status then fails the build until someone classifies it.
