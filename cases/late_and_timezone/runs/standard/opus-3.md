claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 75 s | USD 0.277 | 2026-10-14 | project ad0b16b | prompt 150d20052204

# Diagnosis: `daily_revenue` misses the backfill, and one backfill order has the wrong date

## 1. Root cause

There are two separate problems. Each explains part of the gap.

**A. `models/daily_revenue.sql` cannot pick up late-arriving orders (most of the gap).**
The incremental filter only rebuilds `order_date >= max(order_date) - 3 days`. The backfill landed last night, but every backfilled order is dated in February, weeks before the window. Those dates were never recomputed, so `daily_revenue` still holds the pre-backfill February numbers. The build log shows no failure because nothing failed. The model did exactly what it was written to do.

**B. The backfill rows in `raw_orders` use a different timestamp convention from the rest of the table.**
The numbers don't match a pure "missed backfill" story:

| | `orders` | `daily_revenue` | diff |
|---|---|---|---|
| Jan | 30 / 502.00 | 29 / 496.00 | **+1 / 6.00** |
| Feb | 32 / 474.00 | 27 / 415.00 | **+5 / 59.00** |

The backfill is 6 orders worth 65.00, but only 5 of them (59.00) are in February in `orders`. The sixth (6.00) has `order_date = 2018-01-31`. The sync screen says that order was placed at 2018-02-01 04:48:19 local, so its `ordered_at` was loaded shifted back past midnight.

Other backfill orders still land on their sync-screen dates:
- order 102 at `2018-02-08 14:00:05` → 8 Feb
- order 104 at `2018-02-22 15:47:33` → 22 Feb

That bounds the shift to between about 4h48m and 8h12m. This fits the backfill being exported in UTC from a store on Perth time (UTC+8, and the shop has WA customers), while the main feed is in shop-local time. The main feed must be local, because ops says `order_date` has matched the local-time admin every day.

The consequences:
- One February order is counted in January.
- Any backfill order placed before 08:00 local is on the wrong day. Order 101 shows 2 Feb, but the sync screen has no 2 Feb order, so it is probably the 3 Feb one.
- The schema docs say `order_date` is "Date (UTC)". That doesn't describe what the data actually is.

**Correct February after both fixes:** 33 orders, 480.00. January stays at 29 / 496.00, which `daily_revenue` already shows.

## 2. Fix

**Confirm the timestamps first.** I'm inferring the backfill IDs are 100–105, since `raw_orders` grew from 99 to 105.

```sql
select id, user_id, ordered_at, cast(ordered_at as date) as order_date
from raw_orders where id >= 100 order by id;
```

Compare these against the sync screen times. If each one is earlier by the same offset, that is the store's UTC offset. If the 04:48:19 order shows `2018-01-31 20:48:19`, the offset is 8h.

**Data fix (B):** have the ingestion job re-deliver the backfill in shop-local time, the same convention as the rest of `raw_orders`. Fixing it at the source is better than hard-coding order IDs in `stg_orders`. If a stopgap is needed before ingestion can re-run:

```sql
update raw_orders set ordered_at = ordered_at + interval 8 hour  -- use the verified offset
where id between 100 and 105;
```

Separately, change the `order_date` descriptions in `models/schema.yml` from "(UTC)" to shop-local time, and ask ingestion to carry a store/timezone column so mixed conventions are detectable.

**Code fix (A):** `daily_revenue` is about 70 rows aggregated from a ~100-row table, so the incremental logic saves nothing and silently drops late data. Make it a table:

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

Also update its description in `schema.yml`, which currently says it "rebuilds the last three days". Then run `dbt build --select stg_orders+`.

If it must stay incremental, the window has to be driven by when rows *arrived* (a `_loaded_at` column from ingestion), not by `order_date`. Without that column, the model has no way to tell that old dates changed.

## 3. Test that would have caught it

Add `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must agree with orders on every order_date
with expected as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

),

actual as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue

from expected

full outer join actual
    on expected.order_date = actual.order_date

where expected.orders is distinct from actual.orders
   or abs(coalesce(expected.revenue, 0) - coalesce(actual.revenue, 0)) > 0.005
```

Last night this would have failed on 6 order dates: 31 Jan, 2/1 Feb, and 8, 13, 22 and 26 Feb.

The timezone shift is harder to catch inside dbt, because nothing in `raw_orders` records which store or timezone a row came from. The durable guard is upstream: ingestion should deliver `ordered_at` as `TIMESTAMPTZ` or with a store/timezone column, and a model contract on `stg_orders` should enforce that type. That way a mixed-convention load fails the build instead of silently shifting dates.
