claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 44 s | USD 0.214 | 2026-10-15 | project ad0b16b | prompt 5e3ee671b766

## 1. Root cause

There are two separate problems. Together they produce the 415.00/27 vs 474.00/32 gap.

**A. `models/daily_revenue.sql`: the incremental filter never picks up the backfill.**

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

Last night's run only rebuilt dates within 3 days of the latest date in the table, which is early April. The six backfilled orders (ids 100–105) are dated 31 Jan through 26 Feb, so `delete+insert` never touched those days. The numbers account for the gap exactly:

| | `orders` | `daily_revenue` | missing |
|---|---|---|---|
| Jan | 30 / 502.00 | 29 / 496.00 | order 100 (6.00 coupon) |
| Feb | 32 / 474.00 | 27 / 415.00 | orders 101–105 (59.00) |
| total | | | 6 orders / 65.00 = the backfill |

Every test passed because no test compares `daily_revenue` to `orders`.

**B. `raw_orders`: the backfilled rows' `ordered_at` is shifted 5h30m early.**

Order 100 is 2018-02-01 04:48:19 on the store's sync screen but 2018-01-31 23:18:19 in `raw_orders`. That is exactly −5:30. `stg_orders.sql` does `cast(ordered_at as date)`, so the order lands on 31 Jan.

The same shift is visible elsewhere. Order 101 is dated 2018-02-02 in `orders`, but the sync screen has no order on the 2nd, only the 3rd. So it also crossed midnight. The existing rows match the shop admin day-for-day, so they are stored in shop-local time. The backfill came in with a different offset. The model code is not the cause here: the data was loaded inconsistently.

Related: `schema.yml` documents `order_date` as "Date (UTC)". The data is actually shop-local, which is how ops uses it. That description is wrong and should be corrected so nobody "fixes" the good rows.

## 2. Fix

**Data.** Correct the backfill at the source: re-export or re-ingest that store's backfill with the same timezone handling as the regular sync. Otherwise the next reload will overwrite any in-warehouse patch. As an interim patch:

```sql
-- check all six against the sync screen first: order 100 is confirmed exactly,
-- order 101 only by date; the others may shift without crossing midnight
update raw_orders
set ordered_at = ordered_at + interval '5 hours 30 minutes'
where id between 100 and 105;
```

**Model.** `daily_revenue` has 69 rows. Incremental buys nothing here, and the lookback window will always miss late-arriving data older than 3 days. I'd make it a plain table:

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

Then run `dbt build --full-refresh --select stg_orders+`. If you keep it incremental, you still need the full refresh now, and you'll need one after every backfill.

**Expected result:** Jan back to 29 / 496.00. Feb goes to 33 / 480.00 (27 + 6, 415.00 + 65.00) in both `orders` and `daily_revenue`. Order 100 moves to 1 Feb and order 101 to 3 Feb, matching the sync screen.

## 3. Test that would have caught it

`tests/assert_daily_revenue_matches_orders.sql`:

```sql
with orders_by_day as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

),

daily as (

    select * from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_expected,
    d.orders as orders_actual,
    o.revenue as revenue_expected,
    d.revenue as revenue_actual

from orders_by_day o
full outer join daily d
    on o.order_date = d.order_date

where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
   or (o.order_date is null) <> (d.order_date is null)
```

This would have failed last night on 2018-01-31 and the five February dates.

The timezone shift itself can't be detected inside the warehouse, because nothing in `raw_orders` records the store's local time. To guard against it, the ingestion job should load the source timestamp with its offset (or a `timestamptz`) plus a store id. You can then test that `order_date`, computed in the shop's timezone, matches the source's local date. Until then, ops' reconciliation against the shop admin is the check for that part.
