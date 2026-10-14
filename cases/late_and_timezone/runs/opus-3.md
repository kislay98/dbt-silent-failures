claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 55 s | USD 0.192 | 2026-10-14 | project 8ee5734 | prompt d3281e93889c

# Diagnosis: two separate defects

The six backfilled orders (ids 100–105) expose two problems. One is in the data and one is in `daily_revenue.sql`. Because of the data problem, `orders` is wrong too, not just the dashboard.

## 1. Root cause

### a) Data: the backfill's `ordered_at` values are shifted 5h30m earlier than the rest of `raw_orders`

Ops reconciles `order_date` against the shop admin, which shows local time, and the two have always agreed. So existing `raw_orders.ordered_at` values are in shop-local time. The `schema.yml` descriptions saying "Date (UTC)" are wrong.

The backfill rows are not in local time. The first order was placed at **2018-02-01 04:48:19** local but was loaded as **2018-01-31 23:18:19**, which is 5h30m earlier. Applying the same +5h30m to every backfill row reproduces the sync screen's dates exactly:

| order_id | loaded `ordered_at` | +5h30m | sync screen |
|---|---|---|---|
| 100 | 2018-01-31 23:18:19 | **02-01** 04:48:19 | 1 Feb ✓ |
| 101 | 2018-02-02 20:42:44 | **02-03** 02:12:44 | 3 Feb ✓ |
| 102 | 2018-02-08 14:00:05 | 02-08 19:30:05 | 8 Feb ✓ |
| 103 | 2018-02-13 07:35:51 | 02-13 13:05:51 | 13 Feb ✓ |
| 104 | 2018-02-22 15:47:33 | 02-22 21:17:33 | 22 Feb ✓ |
| 105 | 2018-02-25 19:29:02 | **02-26** 00:59:02 | 26 Feb ✓ |

The backfill was most likely exported in a different timezone, probably UTC, from the store's sync. `stg_orders.sql` does a plain `cast(ordered_at as date)`, so three orders land on the wrong day, and order 100 lands in the wrong month.

This explains why `orders` shows January as 30 orders / 502.00. It should be 29 / 496.00. `customers` is affected too: customer 41's `first_order`, customer 2's `most_recent_order` and customer 77's dates are each one day early.

### b) Code: `models/daily_revenue.sql` never processes late-arriving orders

The incremental filter only rebuilds `order_date >= max(order_date) - 3 days`, which here means dates on or after 2018-04-06. The backfill rows are dated January and February, so they are never selected. The delete+insert never touches those dates.

That is why `daily_revenue` still shows the pre-backfill numbers. The six mismatched days in your diff are exactly orders 100–105, and they add up to 65.00. All the existing tests pass because each one looks at a single model in isolation.

The two defects together explain the gap:
- `daily_revenue` is missing the backfill entirely.
- `orders` has the backfill, but one order is in January.

The correct figures for both models are:
- **January:** 29 orders, 496.00
- **February:** 33 orders, 480.00

## 2. Fix

**Data.** The proper fix is for the ingestion job to re-export the backfill in shop-local time, like the rest of `raw_orders`, and reload it. As a stopgap, once you've confirmed the ids against the store's sync screen:

```sql
update raw_orders
set ordered_at = ordered_at + interval '5 hours 30 minutes'
where id between 100 and 105;
```

`raw_orders` has no store or batch column, so `stg_orders` has no reliable way to tell these rows apart. That is why this belongs in the data or ingestion layer, not in staging.

**Code.** `orders` is rebuilt in full on every run, so making `daily_revenue` incremental saves nothing, and it silently drops anything that arrives late. Make it a table:

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

Also update its `schema.yml` description so it no longer mentions the three-day rebuild. While you're there, change "Date (UTC)" to local time for `order_date`, `first_order` and `most_recent_order`.

Then run `dbt build`. The materialization change replaces the existing incremental table.

If you must keep it incremental, you need a load timestamp in `raw_orders` to filter on, rather than `order_date`. Until then, run `--full-refresh -s daily_revenue` after every backfill.

## 3. Test that would have caught it

Add a reconciliation test, `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with orders_by_date as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

)

select
    order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily_revenue

from orders_by_date o

full outer join {{ ref('daily_revenue') }} d using (order_date)

where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Last night this test would have failed on six dates.

The timezone shift is harder to catch, because the shifted timestamps are still plausible values. No schema test on `ordered_at` can detect it. The durable guard is at ingestion: load `ordered_at` as `TIMESTAMPTZ`, or with an explicit offset column, and reject batches without one. Until then, the only check that catches it is the one ops already does: reconciling `orders` by day against the shop admin.
