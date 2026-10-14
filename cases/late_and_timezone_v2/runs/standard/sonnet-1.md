claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 34 s | USD 0.100 | 2026-10-15 | project ad0b16b | prompt 5e3ee671b766

## 1. Root cause

There are two problems, and the second is what hid the first.

**a) `models/daily_revenue.sql`: the incremental window can't see late-arriving rows.**
- On each run it only rebuilds `order_date >= max(order_date) - 3 days`. The max is in April, so only the last few days of April are reprocessed.
- The backfill landed in January and February, so those days were never rebuilt. The ingestion job only reloads the raw tables, so `daily_revenue` kept its old rows.
- `orders` is a full table and picked the rows up, so it now has 6 more orders worth 65.00 than the dashboard:
  - February: 32 vs 27 orders, 474 vs 415 (5 orders, 59.00).
  - January: 30 vs 29 orders, 502 vs 496 (6.00, which is order 100).
- The log shows `daily_revenue` is "OK" and its tests pass. `unique`/`not_null` can't notice missing rows, and no test compares it to `orders`.

**b) The backfilled rows in `raw_orders` (ids 100–105, from the store that was down) have `ordered_at` in UTC.**
- `stg_orders.sql` does `cast(ordered_at as date)` and assumes `ordered_at` is already in the shop's local time. That's why `orders` matched the admin until this load.
- Order 100 is `2018-01-31 23:18:19` in the warehouse but `2018-02-01 04:48:19` on the sync screen. That's exactly +5:30, so the shop is on IST and the backfill is 5h30m behind.
- Order 100 therefore falls on 31 January instead of 1 February. Order 101 shows as 2 February in the warehouse but 3 February on the screen, which fits the same shift if it was placed after 18:30 UTC.
- Even after (a) is fixed, `orders` by `order_date` still wouldn't match the admin for these rows.
- I'm inferring that ids 100–105 are the backfill and that the shift applies to all of them. I can't see `raw_orders` itself, so check that before applying the fix.

## 2. Fix

**Data (ingestion/seed):** load the backfill with `ordered_at` converted to shop local time, i.e. add 5.5 hours to ids 100–105. For example:

```sql
update raw_orders set ordered_at = ordered_at + interval 330 minute where id between 100 and 105;
```

Better, fix it in the ingestion job so the column has one timezone. Afterwards order 100 is dated 1 Feb and `orders` February becomes 33 orders / 480.00, which should match the admin day by day.

**Model:** `daily_revenue` is about 70 rows, so drop the incremental logic and make it a plain table:

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

If it must stay incremental, add a `loaded_at` to the raw data and filter on that. Otherwise schedule periodic `--full-refresh` runs.

Rebuild now with `dbt build --full-refresh -s daily_revenue+` (after the data fix), so the stale rows are replaced.

## 3. Tests

**Reconciliation test** (`tests/assert_daily_revenue_matches_orders.sql`), which would have failed on this load:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as dash_orders, o.revenue, d.revenue as dash_revenue
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where coalesce(o.orders, 0) <> coalesce(d.orders, 0)
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

**Timezone check:** the timezone mismatch needs its own guard, for example a source column carrying the store's UTC offset. Lacking that, a pinned singular test on a known order, such as `order_id = 100` must have `order_date = '2018-02-01'`, would catch this specific case.

I haven't run any of this. It's based on the files and output you pasted.
