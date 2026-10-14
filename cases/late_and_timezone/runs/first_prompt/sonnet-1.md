claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 32 s | USD 0.089 | 2026-10-14 | project 8ee5734 | prompt 0a1f2799e0c1

## 1. Root cause

`models/daily_revenue.sql` is the problem, and the data that exposes it is the backfill of orders 100-105 in `raw_orders`.

The model is incremental with `delete+insert`. On each run it only reprocesses `order_date >= max(order_date) - 3 days` of its own table. The max is 2018-04-09, so the window starts around 2018-04-06. The comment assumes late data lands within a day or two, but the filter keys on the order's date, not on when the row arrived.

The backfilled orders are dated 2018-01-31 to 2018-02-25, far outside that window. `orders` is a full table and picks them up. `daily_revenue` never revisits those dates, so it keeps the stale partial rows.

The reconciliation query matches this exactly. Six dates are short by exactly one order each, and the missing amounts are 6 + 12 + 9 + 15 + 20 + 3 = 65.00.

| Order | Date (UTC) | Amount |
|---|---|---|
| 100 | 01-31 | 6.00 |
| 101 | 02-02 | 12.00 |
| 102 | 02-08 | 9.00 |
| 103 | 02-13 | 15.00 |
| 104 | 02-22 | 20.00 |
| 105 | 02-25 | 3.00 |

Five of those are in February (59.00), which gives 474 − 59 = 415 and 32 − 5 = 27. Order 100 explains January being short by 1 order and 6.00 (30 vs 29, 502 vs 496).

Every test passed because the tests only check uniqueness and not-null. Nothing compares `daily_revenue` to `orders`.

**A second issue ops will hit next:** the sync screen's dates are the shop's local time, and that is not UTC.
- Order 100 is `2018-01-31 23:18:19` in the warehouse, but the screen shows it at 04:48:19 on 1 Feb. That is a +5:30 offset.
- The other five orders fit the same offset: 20:42 UTC becomes 3 Feb, 19:29 UTC becomes 26 Feb, and so on.
- `order_date` is `cast(ordered_at as date)`, documented as UTC, so order 100 lands in January.
- After the fix below, Feb will read 32 / 474.00 in the warehouse against 33 / 480.00 (27 + 6 orders, 415 + 65) in the shop admin. That difference is timezone, not a bug.

## 2. Fix

**Repair now:**
```
dbt run --full-refresh -s daily_revenue
```

**Make it durable.** The table is a one-pass aggregate over `orders`, and `raw_orders` has no load timestamp to drive a reliable incremental. The simplest correct option is to drop the incremental config in `daily_revenue.sql`:

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

If the table gets large, switch to incremental on a real `loaded_at` watermark from ingestion instead.

**For ops' reconciliation:** keep `order_date` as UTC, since `customers.first_order` and the docs depend on that. Add a local-date column in `stg_orders.sql`:

```sql
cast(ordered_at as date) as order_date,
cast(ordered_at + interval '5 hours 30 minutes' as date) as order_date_local,
```

Ops can then reconcile on `order_date_local`. The offset is inferred from the six orders, so please confirm it is the shop's timezone (Asia/Kolkata has no DST, so a fixed offset is safe). It is hardcoded because `raw_orders` has no store or timezone column. If more stores with other timezones are coming, that needs to be added upstream.

## 3. Test

Add `tests/assert_daily_revenue_matches_orders.sql`. It fails on any date where the two models disagree, including dates missing from either side:

```sql
with o as (
    select order_date, count(*) as orders, coalesce(sum(amount), 0) as revenue
    from {{ ref('orders') }}
    group by 1
)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily_revenue
from o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where coalesce(o.orders, 0) <> coalesce(d.orders, 0)
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Against last night's build it would have returned the six rows from your diff query. I haven't run any of this against the warehouse, since I only had the pasted output.
