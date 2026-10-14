claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 57 s | USD 0.198 | 2026-10-14 | project 8ee5734 | prompt d3281e93889c

# Root cause

**There are two problems, and neither published number is right.** The correct February figure is **33 orders / 480.00** (27 / 415.00 before the backfill, plus 6 / 65.00). `orders` shows 32 / 474.00 and `daily_revenue` shows 27 / 415.00.

## 1. `models/daily_revenue.sql` never sees the backfill

The incremental filter only rebuilds dates within 3 days of the newest date already in the table:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`max(order_date)` is 2018-04-09, so each run rebuilds only 04-06 onward. The backfilled orders are dated 31 Jan to 25 Feb, so they are filtered out and `daily_revenue` stays at its pre-backfill state. That is why its Feb total is exactly the old 27 / 415.00, and every mismatched day is short by exactly one order. `raw_orders` has no load timestamp, so this model has no way to detect late-arriving history. The 3-day lookback only covers late payments.

## 2. The backfill rows in `raw_orders` (ids 100–105) are shifted by −5h30m

The first order is placed at **2018-02-01 04:48:19** in the store's time, but it is loaded as **2018-01-31 23:18:19**. Adding 5h30m to every backfilled row reproduces the store's sync screen exactly:

| id | loaded `ordered_at` | +5:30 | sync screen |
|---|---|---|---|
| 100 | 01-31 23:18:19 | **02-01** 04:48 | 1 Feb |
| 101 | 02-02 20:42:44 | **02-03** 02:12 | 3 Feb |
| 102 | 02-08 14:00:05 | 02-08 19:30 | 8 Feb |
| 103 | 02-13 07:35:51 | 02-13 13:05 | 13 Feb |
| 104 | 02-22 15:47:33 | 02-22 21:17 | 22 Feb |
| 105 | 02-25 19:29:02 | **02-26** 00:59 | 26 Feb |

Because of the shift, `stg_orders.order_date` (a plain `cast(ordered_at as date)`) puts order 100 (6.00) in January. That explains why `orders` has 32 in Feb and 30 in Jan instead of 33 and 29. It also puts orders 101 and 105 on the wrong days. The ingestion job wrote this store's backfill in a different time convention from the rest of the table. The column is `TIMESTAMP` with no zone, so nothing downstream can detect the difference.

The error also reaches `customers`: customer 41's `first_order` should be 02-01, customer 2's `most_recent_order` 02-03, and customer 77's 02-26.

One thing to check with the ingestion owner: `schema.yml` documents `order_date` as UTC. However, the existing rows have always matched the shop admin's local dates, so they appear to be stored in shop-local time. Either the docs are wrong or the shop is on UTC. The 6 backfill rows are the odd ones out either way, but someone should confirm which convention is canonical.

# Fix

**Data.** Fix the backfill in the ingestion job and reload. If you only patch the warehouse, the next full reload will reintroduce the error. As a stopgap:

```sql
update raw_orders
set ordered_at = ordered_at + interval '5 hours 30 minutes'
where id between 100 and 105;
```

**Model.** Rebuild `daily_revenue` from scratch so late history is always picked up. At this size, incremental saves nothing:

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

If it has to stay incremental, add a `_loaded_at` column to `raw_orders` and `raw_payments` and filter on that instead of `order_date`. With `delete+insert` keyed on `order_date`, you would also need to rebuild any date an order moved *off*, otherwise stale rows remain.

Then run `dbt build --full-refresh --select daily_revenue+` (or `+daily_revenue+` after the data fix). The expected result is Jan 29 / 496.00 and Feb 33 / 480.00 in both tables.

Update the `daily_revenue` description in `schema.yml` too, since it currently says "Incremental, rebuilds the last three days".

# Tests

**Reconciliation test.** This would have failed last night on 6 dates. Save it as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with o as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

),

d as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily_revenue

from o
full outer join d
    on o.order_date = d.order_date

where o.order_date is null
   or d.order_date is null
   or o.orders <> d.orders
   or abs(o.revenue - d.revenue) > 0.005
```

**The timestamp shift.** No test can catch this from the warehouse alone, because the shifted rows are internally consistent and every dbt test passed on them. You need a source of truth to compare against:

- Have ingestion load the store's own local timestamp or date alongside `ordered_at` (as `TIMESTAMPTZ`, or as `store_order_date`).
- Add a test that `cast(ordered_at as date) = store_order_date` returns no rows.
- Alternatively, load the shop admin's daily counts as a source and reconcile `orders` against them per `order_date`. This automates the check ops currently does by hand.
