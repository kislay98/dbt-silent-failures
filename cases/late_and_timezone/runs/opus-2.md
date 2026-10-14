claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 60 s | USD 0.191 | 2026-10-14 | project 8ee5734 | prompt d3281e93889c

# February mismatch between `orders` and `daily_revenue`: root cause and fix

There are two separate faults. Ops was expecting `orders` to show 32 / 474.00, but that number is also wrong. The correct February is **33 orders / 480.00**, and January should stay at **29 / 496.00**.

## 1. Root cause

### A. `models/daily_revenue.sql`: the incremental window never saw the backfill

The `is_incremental()` filter only rebuilds `order_date >= max(order_date) - 3 days`, which is the start of April. The six backfilled orders (ids 100–105) are dated 31 Jan to 25 Feb, so `daily_revenue` never picked them up.

The diff query confirms this. The per-day gaps are 6 + 12 + 9 + 15 + 20 + 3, which is exactly the 6 orders and 65.00 of the backfill. February is 5 orders and 59.00 short, and January is 1 order and 6.00 short. `daily_revenue` is simply the pre-backfill state.

The tests pass because they only check uniqueness and nulls within each model. Nothing compares the two models.

### B. `raw_orders`: the backfill's `ordered_at` is shifted 5h30m too early

The store's sync screen says the first order was placed at `2018-02-01 04:48:19`. The warehouse has `2018-01-31 23:18:19`, which is exactly 5h30m earlier. The same offset applies to all six rows:

| id | raw `ordered_at` | +5h30m (sync screen) | `order_date` now → should be |
|---|---|---|---|
| 100 | 01-31 23:18:19 | 02-01 04:48:19 | **01-31 → 02-01** |
| 101 | 02-02 20:42:44 | 02-03 02:12:44 | **02-02 → 02-03** |
| 102 | 02-08 14:00:05 | 02-08 19:30:05 | 02-08 |
| 103 | 02-13 07:35:51 | 02-13 13:05:51 | 02-13 |
| 104 | 02-22 15:47:33 | 02-22 21:17:33 | 02-22 |
| 105 | 02-25 19:29:02 | 02-26 00:59:02 | **02-25 → 02-26** |

With the shift applied, the dates match the sync screen's 1, 3, 8, 13, 22 and 26 February. Every other row in `raw_orders` already matches the shop admin's dates, so the ingestion job applied a timezone conversion to this backfill that it doesn't apply to normal loads.

This is why `orders` shows only 5 of the 6 backfilled orders in February: order 100 slipped into January. Three days won't reconcile against the shop admin. The error also reaches `customers`:
* customer 41's `first_order` shows 2018-01-31 instead of 02-01.
* customer 2's `most_recent_order` shows 02-02 instead of 02-03.
* customer 77's `first_order`/`most_recent_order` show 02-25 instead of 02-26.

`stg_orders.sql` is fine. `cast(ordered_at as date)` is correct as long as the raw data is consistent.

A side note: `schema.yml` describes `order_date` as "Date (UTC)", but ops reconciles it against shop-local dates and it has always matched. Either the shop runs on UTC or that description is wrong, so it's worth confirming.

## 2. Fix

**Data.** The proper fix is to re-run the backfill load without the offset conversion and fix that path in the ingestion job. As a stopgap, check first that ids 100–105 are exactly the six backfilled rows, then correct them in the warehouse:

```sql
update raw_orders
set ordered_at = ordered_at + interval '5 hours 30 minutes'
where id between 100 and 105;
```

**Model.** Late-arriving history will happen again, and a lookback on `order_date` can't catch it. At this data size, the simplest robust option is to stop making `daily_revenue` incremental:

```sql
{{
    config(
        materialized='table'
    )
}}

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

If it has to stay incremental, the ingestion job needs to add a `_loaded_at` column. The model would then rebuild every `order_date` that has rows loaded since the last run, instead of the last three order dates.

**Rebuild:**
```
dbt build --select stg_orders+ --full-refresh
```
Expected result: Jan 29 / 496.00, Feb 33 / 480.00, and both models agree.

## 3. Tests that would have caught it

**Reconciliation between the models** (`tests/assert_daily_revenue_matches_orders.sql`). This would have failed last night on six dates:

```sql
with expected as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(expected.order_date, daily.order_date) as order_date,
    expected.orders as expected_orders,
    daily.orders,
    expected.revenue as expected_revenue,
    daily.revenue

from expected

full outer join {{ ref('daily_revenue') }} as daily
    on expected.order_date = daily.order_date

where expected.orders is distinct from daily.orders
   or abs(coalesce(expected.revenue, 0) - coalesce(daily.revenue, 0)) > 0.005
```

**Timestamp shift.** The current columns can't reveal this; a 23:18 timestamp looks as plausible as 04:48. To make it testable, have ingestion also land the source's local timestamp or date as received, for example `ordered_at_source`. Then add a test that compares it with the converted value:

```sql
-- tests/assert_order_date_matches_source.sql
select id, ordered_at, ordered_at_source
from {{ source('shop', 'raw_orders') }}
where cast(ordered_at as date) <> cast(ordered_at_source as date)
```

Until that column exists, the cross-model reconciliation test plus ops' daily reconciliation against the shop admin are the only safeguards against this kind of shift.
