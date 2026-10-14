claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 30 s | USD 0.085 | 2026-10-14 | project 8ee5734 | prompt 0a1f2799e0c1

## 1. Root cause

**`models/daily_revenue.sql`**: the incremental filter only reprocesses the last three days relative to the table's own maximum date:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The comment says this exists because payments can land a day or two late. That assumption doesn't hold for this load. The latest order in the data is 2018-04-09, so the window starts around 2018-04-06. The backfilled orders 100-105 arrived last night with `ordered_at` values in January and February. They are in `orders`, which is a full table rebuild, but `daily_revenue` never reprocesses those dates. The result:

- Dates with only a backfilled order (2018-02-22) are missing from `daily_revenue` entirely.
- Dates that already had orders (01-31, 02-02, 02-08, 02-13, 02-25) are stale: they have one fewer order each.
- The gap is 32 vs 27 orders and 474 vs 415 in Feb, plus 30 vs 29 and 502 vs 496 in Jan.

All 35 tests pass because none compares `daily_revenue` with `orders`. `unique` and `not_null` hold on a table that is merely incomplete.

**A second problem, which is why ops' "expected" numbers also don't line up.** `stg_orders.order_date` is `cast(ordered_at as date)`, and `ordered_at` is UTC (the schema says so). The shop admin and the sync screen use local time. Comparing the sync screen's first order (local 2018-02-01 04:48:19) with order 100 (UTC 2018-01-31 23:18:19) gives an offset of +5:30. I checked this against all six orders:

| order | UTC | +5:30 | sync screen |
|---|---|---|---|
| 100 | 01-31 23:18 | 02-01 | 1 Feb |
| 101 | 02-02 20:42 | 02-03 | 3 Feb |
| 102 | 02-08 14:00 | 02-08 | 8 Feb |
| 103 | 02-13 07:35 | 02-13 | 13 Feb |
| 104 | 02-22 15:47 | 02-22 | 22 Feb |
| 105 | 02-25 19:29 | 02-26 | 26 Feb |

So in `orders`, order 100 sits in January (+1 order, +6.00 in Jan) and only five orders (59.00) land in February. Ops expects six orders and 65.00 in February, so even after the `daily_revenue` fix, a UTC `orders` will still disagree with the shop admin by one order. The offset is inferred from these six rows, so confirm the shop's timezone (Asia/Kolkata?) before relying on it.

## 2. Fix

**a) `daily_revenue`.** The model is a cheap aggregate over a small table, and `raw_orders` has no load timestamp to drive a reliable lookback. The simplest correct fix is to stop making it incremental:

```sql
{{ config(materialized='table') }}
```

Remove the `is_incremental()` block, then run `dbt run --full-refresh -s daily_revenue`. The change to `table` already forces a rebuild, but the flag makes it explicit. If it must stay incremental, the lookback has to key on when rows arrived (a `loaded_at` column), not on `max(order_date)`. Update the model description in `schema.yml`, which currently says it rebuilds the last three days.

**b) Local dates.** Don't redefine `order_date` silently, because that would shift `customers.first_order`, `most_recent_order` and all history. Add a local date alongside it in `stg_orders.sql`:

```sql
cast(ordered_at as date) as order_date,
cast(timezone('Asia/Kolkata', ordered_at at time zone 'UTC') as date) as order_date_local,
```

Then pass it through `orders` and have ops reconcile on `order_date_local` (or make `daily_revenue` group by it, if the dashboard is meant to follow shop time). After this, February by local date should show 32 + 1 = 33 orders in `orders` once the January order is moved, and ops' 65.00 for the six backfilled orders should reconcile.

## 3. Test that would have caught this

A singular test, `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with o as (
    select order_date, count(*) as n, sum(amount) as amt
    from {{ ref('orders') }}
    group by 1
),

d as (
    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}
)

select coalesce(o.order_date, d.order_date) as order_date, o.n, d.orders, o.amt, d.revenue
from o
full outer join d on o.order_date = d.order_date
where coalesce(o.n, 0) <> coalesce(d.orders, 0)
   or abs(coalesce(o.amt, 0) - coalesce(d.revenue, 0)) > 0.005
```

Run against last night's build, this returns exactly the six rows in your diff query and fails the build. The full outer join also catches dates missing from either side, such as 02-22.

I only had the files and output you pasted, not the seed data or a live project, so I haven't run any of this.
