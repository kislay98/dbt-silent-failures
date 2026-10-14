claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 36 s | USD 0.107 | 2026-10-14 | project ad0b16b | prompt 150d20052204

# Root cause

There are two separate problems. One explains why the dashboard disagrees with `orders`, the other why `orders` has a stray order in January.

**1. `models/daily_revenue.sql` never picks up late-arriving history.**
- It is incremental with `delete+insert`, and the `is_incremental()` filter only re-reads `order_date >= max(order_date) - 3 days`.
- `max(order_date)` is in April, so only about the last three days get rebuilt.
- The backfill arrived last night with February `order_date`s. Those rows are in `orders` (the table is rebuilt fully), but the filter skips them, so `daily_revenue` still holds the old February: 27 orders and 415.00.
- The comment says the window exists for payments landing late. That is a different problem from backfilled orders, and the window is anchored to the order date, not to when the data arrived.

**2. Timezone mismatch in the backfilled `raw_orders` rows, surfaced through `models/staging/stg_orders.sql`.**
- `stg_orders` does `cast(ordered_at as date)` with no timezone handling, and `schema.yml` calls that "Date (UTC)".
- The numbers show one backfilled order, worth 6.00, sitting in January. `orders` has January at 30 orders and 502.0, while `daily_revenue` (frozen from before the load) has 29 and 496.0.
- That is the order the sync screen lists on 1 Feb at 04:48:19. It is loaded as roughly 2018-01-31 17:48:19, which looks like shop-local time (about UTC+11, matching the AU regions) converted to UTC. It therefore lands on 31 Jan.
- Ops said history matched the shop admin day for day. That means the historical `ordered_at` values follow the shop-local convention, and the backfill used a different one.
- The arithmetic checks out. The six orders are worth 65.00. Five of them are in February for +59.00, which is exactly 474.00 − 415.00 and 32 − 27 orders. The sixth is in January for 6.00.

I can't see the seed data, so the timezone part is inferred. You can confirm it with:
```sql
select * from raw_orders where ordered_at >= '2018-01-31' and ordered_at < '2018-02-01 06:00' order by ordered_at;
```
The backfilled order should appear at about 17:48:19 on 31 Jan.

# Fix

1. **Normalize the backfill to the same convention as the historical rows.** The cleanest way is to fix the ingestion job and reload. As a one-off data fix, shift the backfilled rows by the offset (+11h for that order, which gives 2018-02-01 04:48:19). Check the offset for each row against DST, since Sydney is +10 or +11 depending on the date. If you would rather handle it in the model, add a source-timezone flag to the raw table and convert explicitly in `stg_orders`, then document it:
   ```sql
   cast(ordered_at at time zone 'UTC' at time zone 'Australia/Sydney' as date) as order_date
   ```
   Only use this if all rows are genuinely UTC. Applying it to rows that are already local would break history.

2. **Make `daily_revenue` robust to backfills.** It is 69 rows, so the simplest option is to drop the incremental logic:
   ```sql
   {{ config(materialized='table') }}
   ```
   Keep the rest of the model and remove the `{% if is_incremental() %}` block. Then run `dbt run --full-refresh -s daily_revenue` once. If it must stay incremental, filter on an ingestion timestamp (such as a `_loaded_at` column) rather than on `order_date`, or schedule periodic full refreshes.

After both fixes, expected February is 33 orders and 480.00 (27 + 6 orders, 415.00 + 65.00). January goes back to 29 orders and 496.00.

# Test

The current tests only check uniqueness and nulls, so nothing compares the two models. Add `tests/assert_daily_revenue_matches_orders.sql`:
```sql
with d as (
    select order_date, orders, revenue from {{ ref('daily_revenue') }}
),
o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)
select coalesce(d.order_date, o.order_date) as order_date, d.orders, o.orders as orders_expected, d.revenue, o.revenue as revenue_expected
from d
full outer join o on d.order_date = o.order_date
where d.order_date is null
   or o.order_date is null
   or d.orders != o.orders
   or abs(d.revenue - o.revenue) > 0.005
```
This would have failed on last night's build with the February mismatch. For the timezone issue, add a source freshness-style check, for example a test that the earliest `ordered_at` hour distribution of newly loaded rows matches history. A simpler guard is to reconcile `orders` by day against the shop admin export as a seed or `dbt_utils.equality` check.
