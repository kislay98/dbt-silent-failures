claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 32 s | USD 0.101 | 2026-10-14 | project ad0b16b | prompt 150d20052204

## 1. Root cause

**`models/daily_revenue.sql`** is the main cause. It is incremental with `delete+insert` and only rebuilds `order_date >= max(order_date) - 3 days`. Its comment assumes late data lands "a day or two" after the order. The backfill added orders dated 1-26 Feb while the max date is in early April, so the window starts around 4 April and never touches them. `orders` (a full-refresh table) picked them up, `daily_revenue` did not. The tests passed because they only check `unique` and `not_null` on `daily_revenue`, and nothing compares it to `orders`.

The Feb/Jan split in your numbers points to a second problem, in the backfilled data. I can't see the seed rows, so this part is inferred from the arithmetic and needs checking.

| | orders | daily_revenue | gap |
|---|---|---|---|
| Feb | 32 / 474.00 | 27 / 415.00 | 5 / 59.00 |
| Jan | 30 / 502.00 | 29 / 496.00 | 1 / 6.00 |

That is 6 orders and 65.00 in total, so one backfilled order is dated in **January** in `orders`, even though the sync screen lists all six in February. It is almost certainly the 1 Feb 04:48:19 order. It is the only one early enough in the day to cross midnight if the backfill's `ordered_at` is in a different timezone from the historic rows. At UTC+11 (AEDT), a local time before 11:00 falls on the previous UTC date.

`stg_orders.sql` uses `cast(ordered_at as date)` with no timezone handling, and `raw_orders.ordered_at` is a plain `TIMESTAMP`. So the shop-local historic data and the backfill can't be told apart. `schema.yml` also documents `order_date` as UTC, while ops reconcile against local time.

To confirm, run:
```sql
select id, ordered_at, cast(ordered_at as date) from raw_orders
where id in (<the six backfilled ids>);
```
You should see one row dated 2018-01-31.

## 2. Fix

**a) Make `daily_revenue` correct by construction.** It is about 70 rows, so incremental buys nothing. In `daily_revenue.sql`, replace the config with `materialized='table'` and drop the `is_incremental()` block. If it must stay incremental, filter on a load timestamp from the ingestion job rather than a date window.

**b) Rebuild now.** `dbt run --full-refresh -s daily_revenue` (or just `dbt build`, once it is a table).

**c) Fix the backfill's timestamps.** Ask the ingestion job to load `ordered_at` in the same convention as the historic rows, or correct the six rows in `raw_orders` to the shop-local times the sync screen shows. Then `orders` puts that order on 1 Feb, and Feb should reconcile at 32 / 474.00 in both `orders` and `daily_revenue`. If instead the source is genuinely UTC and ops want local days, convert explicitly in `stg_orders` and update the `schema.yml` description:
```sql
cast(ordered_at at time zone 'UTC' at time zone 'Australia/Sydney' as date) as order_date
```
That would shift the historic rows too, so only do it after confirming what the historic data is.

## 3. Test

A singular test, `tests/assert_daily_revenue_matches_orders.sql`, returns rows only when the two disagree per day:
```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
)
select coalesce(o.order_date, d.order_date) as order_date,
       o.orders as expected_orders, d.orders as actual_orders,
       o.revenue as expected_revenue, d.revenue as actual_revenue
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```
This would have failed on last night's build, as 5 of the February days and 31 January would differ.

For the timezone issue, add a test that new orders don't land outside the expected range or hour distribution. A simple check is a `dbt_utils.accepted_range` on `order_date`, or a freshness/volume check on `raw_orders` per load, so a backfill with a different timestamp convention gets flagged.

I haven't changed any files or run anything. The above comes from reading the project and output you provided.
