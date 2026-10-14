claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 33 s | USD 0.102 | 2026-10-14 | project ad0b16b | prompt 1c80550dffb3

## 1. Root cause

There are two independent defects, and together they explain both wrong totals. I can't see the seed rows, so the first is inferred from the numbers and should be confirmed with the query below.

**A. Duplicate payments in `seeds/raw_payments.csv` (flows through `stg_payments.sql` into `orders.sql`)**

- The backfill appears to have re-delivered payments under new `id`s. The seed has 131 rows, and the standard jaffle set is 113, so ids 114–131 look like the late load.
- `unique(payment_id)` passes because the ids are new. Nothing checks the natural key (`order_id`, `payment_method`, `amount`).
- `orders.sql` sums every payment row. That is why order 1 shows 20.00 when 10.00 was charged. The same inflation reaches `customers.customer_lifetime_value` and `region_revenue`, which both total 1917.00, and it also inflates `fees`.
- The excess is 1917 − 1737 = **180.00**.

**B. Stale `daily_revenue` in `models/daily_revenue.sql`**

- The incremental filter `order_date >= max(order_date) - interval 3 day` only rebuilds the last 3 days.
- The backfilled February orders and the late payments are far older than that, so `delete+insert` never touches them.
- The numbers show it. Feb has 33 orders in `orders` but 27 in `daily_revenue`, so 6 orders are missing. March has the same order count (35) but 57.00 less revenue, and Jan is 74.00 short.
- `daily_revenue` is 231.00 below `orders` (1917 − 1686), but the correct figure is 1737. That is 51.00 below the settlement, so the dashboard is wrong in the opposite direction from the other tables.

`dbt build` passes because no test compares anything to a source of truth, and the existing tests only check nulls, uniqueness of ids and accepted values.

To confirm A, list the duplicates and check that their total is 180.00:

```sql
select order_id, payment_method, amount/100.0 as amount,
       count(*) as n, list(id order by id) as payment_ids
from raw_payments
group by 1,2,3
having count(*) > 1
order by order_id;
```

Equal repeat payments can be legitimate, so reconcile this list against the processor settlement before deleting anything.

## 2. Fix

**Data:** remove the duplicate rows (the later ids from the query above) from `raw_payments`, or have the source system fix the re-delivery. After that, `orders` sums to 1737.00.

**Code:** `daily_revenue` is only 69 rows, so the incremental logic isn't worth keeping. Make it a table in `daily_revenue.sql`:

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

If it must stay incremental, rebuild from the earliest changed date instead of a fixed 3 days. Then run a one-time `dbt build --full-refresh -s daily_revenue+`. The existing table won't self-heal, because the old Jan–Feb rows are never reprocessed.

## 3. Tests

Singular tests in `tests/`:

`tests/assert_stg_payments_no_duplicate_payments.sql`

```sql
select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

Set `{{ config(severity='warn') }}` on it if equal repeat payments are legitimate. In that case, treat the warning as a prompt to reconcile against the processor.

`tests/assert_daily_revenue_matches_orders.sql`

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
)
select *
from o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

This one would have failed on Jan, Feb and Mar. The duplicate-payment test would have failed on the backfill itself.

For a standing check against finance, load the processor settlement total as a seed and add a test that `sum(orders.amount)` equals it. That would have flagged the 180.00 gap on the first run.
