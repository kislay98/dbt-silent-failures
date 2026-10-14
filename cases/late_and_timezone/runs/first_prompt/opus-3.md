claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 52 s | USD 0.197 | 2026-10-14 | project 8ee5734 | prompt 0a1f2799e0c1

## 1. Root cause

There are two separate problems. Each one alone would produce a mismatch.

### A. `raw_orders`: the six backfilled rows (ids 100–105) have `ordered_at` shifted 5h30m early

The sync screen says the first backfilled order was placed at **2018-02-01 04:48:19**. The warehouse has order 100 at **2018-01-31 23:18:19**, which is exactly 5:30 earlier. The same offset explains every other row:

| id | loaded `ordered_at` | +05:30 | sync screen |
|---|---|---|---|
| 100 | 01-31 23:18:19 | **02-01** 04:48 | 1 Feb ✓ |
| 101 | 02-02 20:42:44 | **02-03** 02:12 | 3 Feb ✓ |
| 102 | 02-08 14:00:05 | 02-08 19:30 | 8 Feb ✓ |
| 103 | 02-13 07:35:51 | 02-13 13:05 | 13 Feb ✓ |
| 104 | 02-22 15:47:33 | 02-22 21:17 | 22 Feb ✓ |
| 105 | 02-25 19:29:02 | **02-26** 00:59 | 26 Feb ✓ |

The backfill job wrote these timestamps converted out of the store's +05:30 local time. The regular sync loads them as shop wall-clock time, which is why ops' admin reconciliation has always matched. `stg_orders.sql` then does `cast(ordered_at as date)`. As a result, three orders land on the wrong day, and one of them (order 100, 6.00) lands in **January**. That is why `orders` shows January as 30 / 502.00, but it should be 29 / 496.00. The dbt code isn't wrong here; the data is. Side note: the `Date (UTC)` descriptions in `models/schema.yml` don't match how ops actually uses the column, which is shop-local time.

### B. `models/daily_revenue.sql`: the incremental filter can never pick up late-arriving rows

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`max(order_date)` is 2018-04-09, so each run only rebuilds dates from 2018-04-06 onward. The backfill's orders are dated January and February, so they were never merged in. All six (65.00) are missing from `daily_revenue`. The diff query confirms this: every mismatching day is short by exactly one backfilled order (6 + 12 + 9 + 15 + 20 + 3 = 65). The schema tests only check `unique`/`not_null` within each model, so nothing compared the two models and the build was green.

**Neither figure is correct.** With the backfill on the right dates, February should be **33 orders / 480.00** and January **29 / 496.00**. The dashboard's 415.00 is missing the backfill. The `orders` figure of 474.00 has the backfill, but 6.00 of it is in January.

## 2. Fix

**Data:** fix the backfill in the ingestion job so it loads shop-local wall-clock time like the regular sync, then reload. That is the durable fix, because a reload would overwrite any manual patch. As a stopgap in the warehouse, you can run this once. It is guarded so a second run won't shift the rows again:

```sql
update raw_orders
set ordered_at = ordered_at + interval 330 minute
where id between 100 and 105
  and (select ordered_at from raw_orders where id = 100) = timestamp '2018-01-31 23:18:19';
-- check: id 100 should now be 2018-02-01 04:48:19
```

**Model:** `daily_revenue` is a small aggregate over `orders`, and `orders` is already rebuilt in full every run. The incremental strategy saves nothing, and it silently drops backfills. Make it a table:

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

Also update its description in `models/schema.yml` so it no longer says "Incremental, rebuilds the last three days". If you need to keep it incremental, the ingestion job has to add a `_loaded_at` column, and the filter must use that column instead of `order_date`. After that, run `dbt build --full-refresh -s orders+`. The table switch drops the stale incremental table, and `--full-refresh` covers the case where you keep it incremental.

## 3. Test that would have caught it

Add `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must agree with orders for every order_date
with orders as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

),

daily as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(orders.order_date, daily.order_date) as order_date,
    orders.orders as orders_in_orders,
    daily.orders as orders_in_daily_revenue,
    orders.revenue as revenue_in_orders,
    daily.revenue as revenue_in_daily_revenue

from orders

full outer join daily
    on orders.order_date = daily.order_date

where orders.orders is distinct from daily.orders
   or abs(coalesce(orders.revenue, 0) - coalesce(daily.revenue, 0)) > 0.005
```

Last night this test would have failed on the six dates in your diff query. It catches the missing backfill (B). It would **not** catch the timestamp shift (A), because both models inherit the same wrong date. Catching A needs an ingestion-side check: either load `ordered_at` with its UTC offset (`TIMESTAMPTZ`) so the convention is explicit, or have the backfill job reconcile its per-local-date order counts against the sync screen before loading.
