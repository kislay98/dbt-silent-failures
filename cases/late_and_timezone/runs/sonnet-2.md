claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 28 s | USD 0.073 | 2026-10-14 | project 8ee5734 | prompt d3281e93889c

## 1. Root cause

There are two separate faults. Together they produce the mismatch, and no test covers either.

**a) `models/daily_revenue.sql`: the incremental window misses backfilled rows.**
The filter is `order_date >= max(order_date) - 3 days`. The latest order is 2018-04-09, so each run only rebuilds from about 2018-04-06. The six backfilled orders (ids 100-105) are dated Jan 31 to Feb 25, far outside that window. Their dates were never rebuilt, so `daily_revenue` never saw them. That explains the gaps:
- It is short by 1 order in January and 5 in February.
- The missing amounts are 6 + 12 + 9 + 15 + 20 + 3 = 65.00, and the monthly sums reconcile exactly.
- The "payments can land a day or two after the order" comment assumes late data is always recent, and this backfill isn't.

**b) `raw_orders` (ids 100-105): the backfill's `ordered_at` is in UTC, but the rest of the table is in shop local time.**
- The sync screen's first order is placed at 2018-02-01 04:48:19. Order 100 is loaded as `2018-01-31 23:18:19`, which is exactly 5h30 earlier.
- Orders 101-105 have the same 5h30 offset, which gives UTC+05:30. For example, 101 is loaded as Feb 2 20:42 and the sync screen shows it on the 3rd.
- Because `stg_orders` does `cast(ordered_at as date)` with no timezone handling, three orders land on the wrong day in `orders`:
  - 100 lands on Jan 31 instead of Feb 1.
  - 101 lands on Feb 2 instead of Feb 3.
  - 105 lands on Feb 25 instead of Feb 26.
- So `orders` is also wrong against the shop admin, even though it has 32 orders for February. The correct figure is 33 orders and 480.00.
- The `ordered_at` description in `schema.yml` says "Date (UTC)". The data before this load was clearly local time, since ops reconciled it day by day, so that description is misleading.

I inferred all of this from the output you pasted and did not run anything against the warehouse.

## 2. Fix

**Data:** correct the backfill. The proper fix is in the ingestion job, which should load the same timezone as the existing rows. For the data already loaded:

```sql
update raw_orders
set ordered_at = ordered_at + interval 5.5 hour
where id between 100 and 105;
```

The resulting dates should be Feb 1, 3, 8, 13, 22 and 26, matching the sync screen.

**Model:** `daily_revenue` is a trivial aggregate, so the simplest fix is to drop the incremental logic in `daily_revenue.sql`:

```sql
{{ config(materialized='table') }}
```

If it must stay incremental, key the lookback on load time rather than business date, and run a periodic `--full-refresh`. Either way, rebuild now with `dbt build --full-refresh -s orders+`.

After the rebuild, February should be 33 orders and 480.00 in both `orders` and `daily_revenue`.

Also fix the `ordered_at` and `order_date` descriptions in `schema.yml` to say shop local time.

## 3. Tests

**Singular test** `tests/assert_daily_revenue_matches_orders.sql`. It fails on exactly the six rows in your diff query:

```sql
select o.order_date, count(*) as n, sum(o.amount) as amt
from {{ ref('orders') }} o
left join {{ ref('daily_revenue') }} d using (order_date)
group by o.order_date, d.orders, d.revenue
having count(*) <> coalesce(d.orders, 0)
    or abs(sum(o.amount) - coalesce(d.revenue, 0)) > 0.005
```

**For the timezone problem**, add a reconciliation seed of per-day order counts from the shop admin and compare it with `orders`. A cheaper guard is a test that flags a load whose `ordered_at` values are shifted: for example, compare the hour-of-day distribution of newly loaded rows against history, or require the ingestion job to emit an explicit timezone column.
