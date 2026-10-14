claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 34 s | USD 0.084 | 2026-10-14 | project 8ee5734 | prompt d3281e93889c

## 1. Root cause

There are two separate problems, and both involve the six backfilled orders, ids 100-105 in `raw_orders`. That is why the 32 vs 27 gap is exactly those orders and the 474.00 vs 415.00 gap is exactly their 65.00.

**a) `models/daily_revenue.sql`: the incremental window cannot see backfilled data.**
The model only rebuilds `order_date >= max(order_date) - 3 days`. The latest order is on 2018-04-09, so only April is ever rebuilt. The backfill landed on dates from 2018-01-31 to 2018-02-25, well outside that window, so `daily_revenue` never picked it up. Those rows are exactly the six mismatch rows in your diff query. The comment says "payments can land a day or two after the order", but a store sync outage breaks that assumption.

All the tests passed because they only check `unique` and `not_null` on `daily_revenue`. Nothing compares it to `orders`.

**b) `raw_orders.ordered_at` for ids 100-105 is on a different time basis from the rest of the table.**
The stored values are exactly 5h30m behind the shop's local time:

| order | stored `ordered_at` | +5:30 | sync screen |
|---|---|---|---|
| 100 | 2018-01-31 23:18:19 | 2018-02-01 04:48:19 | 1 Feb ✓ |
| 101 | 2018-02-02 20:42:44 | 2018-02-03 02:12:44 | 3 Feb ✓ |
| 105 | 2018-02-25 19:29:02 | 2018-02-26 00:59:02 | 26 Feb ✓ |

Orders 102, 103 and 104 stay on the same day after the shift, which matches the sync screen as well.

`stg_orders.sql` does `cast(ordered_at as date)` with no timezone handling. Ops says every earlier day matched the shop admin, so the existing rows are effectively in shop-local time. The backfill appears to have been loaded in UTC, so three of its orders (100, 101, 105) land on the wrong `order_date`. Order 100 is counted in January (30 orders in `orders`, 29 in `daily_revenue`) when it belongs to 1 Feb.

I'm inferring that the older rows are shop-local from ops' statement. It's worth confirming with the ingestion owner. Note that `schema.yml` documents `order_date` as "Date (UTC)", which contradicts that.

## 2. Fix

**Data:** fix this in the ingestion job. Load ids 100-105 with the same timezone basis as the other rows, or add a `source_tz` or `ordered_at_utc` column and convert explicitly in staging. As a stopgap in `stg_orders.sql`:

```sql
case when id between 100 and 105
     then ordered_at + interval '5 hours 30 minutes'
     else ordered_at
end as ordered_at,
cast(<that expression> as date) as order_date,
```

**Model:** make `daily_revenue` tolerant of late-arriving data. The source is small, so the simplest option is to drop the incremental logic:

```sql
{{ config(materialized='table') }}
```

If it must stay incremental, key the window on a load timestamp from ingestion rather than on `max(order_date)`. Either way, run `dbt build --full-refresh -s stg_orders+` once to repair history.

**Expected result:** January becomes 29 orders / 496.00 and February 33 orders / 480.00. That is 415.00 + 65.00 for February, and January then matches `daily_revenue`'s current 29 / 496.00.

## 3. Test

A singular test, `tests/assert_daily_revenue_matches_orders.sql`, using your diff query:

```sql
select o.order_date
from {{ ref('orders') }} o
left join {{ ref('daily_revenue') }} d using (order_date)
group by o.order_date, d.orders, d.revenue
having count(*) <> coalesce(d.orders, 0)
    or abs(sum(o.amount) - coalesce(d.revenue, 0)) > 0.005
```

It fails today on the six dates above. It also catches the reverse case, where a `daily_revenue` date has no matching orders.

For the timezone problem, add a check in ingestion or a source test that every store's timestamps share one basis. The reconciliation against the shop admin that ops does manually is the other check that would catch it.
