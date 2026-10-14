claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 38 s | USD 0.089 | 2026-10-14 | project 8ee5734 | prompt d3281e93889c

## 1. Root cause

There are two separate defects. Together they explain both the 415 vs 474 gap and why `orders` itself is wrong.

**a) `models/daily_revenue.sql`: the incremental window never reaches the backfill.**
The filter is `order_date >= (select max(order_date) - interval 3 day from {{ this }})`. The newest order is in April (the max is about 2018-04-09), so each run rebuilds only about the last three days. The backfilled orders are dated January/February, so they are never selected. `daily_revenue` is frozen at its pre-backfill state.

That matches the diff output exactly: 27 orders and 415.00 in February, a missing 2018-02-22 row, and short counts on 02-02, 02-08, 02-13 and 02-25. The comment about "payments land a day or two after the order" assumes late data is always recent, and a backfill breaks that.

**b) The backfilled rows in `raw_orders` (ids 100–105) carry UTC timestamps, but the rest of the table is on the shop's local clock.**
Add 5h30m to each loaded `ordered_at` and you get the sync-screen dates:

| order | loaded (UTC) | +5:30 | sync screen |
|---|---|---|---|
| 100 | 2018-01-31 23:18:19 | 2018-02-01 04:48:19 | 1 Feb ✓ (matches the stated 04:48:19) |
| 101 | 02-02 20:42:44 | 02-03 02:12 | 3 Feb ✓ |
| 102 | 02-08 14:00:05 | 02-08 19:30 | 8 Feb ✓ |
| 103 | 02-13 07:35:51 | 02-13 13:05 | 13 Feb ✓ |
| 104 | 02-22 15:47:33 | 02-22 21:17 | 22 Feb ✓ |
| 105 | 02-25 19:29:02 | 02-26 00:59 | 26 Feb ✓ |

`stg_orders.sql` does a plain `cast(ordered_at as date)`, so orders 100, 101 and 105 land a day early: 31 Jan, 2 Feb and 25 Feb instead of 1, 3 and 26 Feb. Orders 102–104 only look right because their local and UTC dates coincide.

This is why `orders` shows February as 32 orders and 474.00 instead of 33 and 480.00. Order 100 (6.00) is counted in January: 30 orders and 502.0 rather than 29 and 496.0.

I inferred the +05:30 offset from the six data points; all six fit exactly. I also inferred that the older rows are on local time from "ops agreed on every day until now". I haven't seen the older raw rows, so confirm this with the ingestion owner. `schema.yml` says `order_date` is "(UTC)", which doesn't match what ops actually reconciles against.

The 28 tests pass because nothing compares `daily_revenue` to `orders`, and `unique`/`not_null` hold on both stale and fresh data.

## 2. Fix

1. **Source data.** Fix it in the ingestion job so every batch uses the same timezone. For the existing rows:
   ```sql
   update raw_orders
   set ordered_at = ordered_at + interval '5 hours 30 minutes'
   where id between 100 and 105;
   ```
   If the loader can't be fixed yet, apply the same shift in `stg_orders` for that batch. That is a stopgap and needs a comment. Also correct the "(UTC)" wording in `schema.yml`.

2. **`daily_revenue`.** Run `dbt run --full-refresh -s daily_revenue` now to rebuild February and January. To stop it recurring, either drop the incremental logic (the data is tiny, so make it a plain table), or key the lookback on load time. For example, add a `loaded_at` to the raw tables and rebuild every `order_date` touched by rows loaded since the last run.

After both changes, February should be 33 orders and 480.00 in both tables. January should be 29 orders and 496.00. The 2018-02-01 row should also exist. `customers.first_order` and `most_recent_order` will shift for customers 41, 2 and 77 (for example, 41 moves to 2018-02-01).

## 3. Test

A singular test, `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
)

select coalesce(o.order_date, d.order_date) as order_date
from o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where coalesce(o.orders, 0) <> coalesce(d.orders, 0)
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

This fails on the six dates listed in your diff output. For the timezone defect, add a reconciliation of daily order counts against a seed of the shop admin's daily counts. The check `orders` per `order_date` against admin counts is exactly what ops does by hand.

I haven't run any of this, since I only have the pasted files and output.
