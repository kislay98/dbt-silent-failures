claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 55 s | USD 0.234 | 2026-10-14 | project ad0b16b | prompt 150d20052204

# Diagnosis: `daily_revenue` vs `orders` for February

There are two separate faults. The incremental model explains why the dashboard still shows the old February numbers. The backfill's timestamps explain why `orders` itself is also wrong.

## 1. Root cause

**First fault: `models/daily_revenue.sql` never picks up the backfill.**

The incremental filter only re-aggregates dates on or after `max(order_date) - 3 days` already in the table:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

That maximum is in April 2018, so last night's run rebuilt only the last few April days. The six backfilled February orders were loaded into `raw_orders`/`raw_payments` but never reached `daily_revenue`. Its January and February rows are exactly what they were before the load:

- `daily_revenue` contains 29 + 27 + 35 + 8 = **99 orders**.
- `raw_orders`, `stg_orders` and `orders` contain **105**.
- The 6 missing orders are ids **100–105**. Their payment ids (114, 117, 118, …) are at the top of the range, which fits a late arrival.

Every test passed because each model is only tested against itself. Nothing compares `daily_revenue` with `orders`.

**Second fault: the backfilled rows in `raw_orders` (ids 100–105) have `ordered_at` on a different clock from the rest of the table.**

All other rows match the shop admin's local dates. The backfill appears to have been exported in UTC. The numbers show this:

- The backfill is 6 orders worth 65.00, all in February according to the sync screen.
- `orders` February is 474 − 415 = **59.00**, with 32 − 27 = **5 orders**.
- `orders` January is 502 − 496 = **6.00**, with 30 − 29 = **1 order**. That is order 100 (a 6.00 coupon payment).
- The sync screen puts order 100 at local time **2018-02-01 04:48:19**. In the warehouse it has become 31 January. That only happens if the stored timestamp is several hours behind local time.
- Order 101 also shows `2018-02-02`, but the sync screen lists the second order on **3 February**.
- Orders 102 (`14:00:05`) and 104 (`15:47:33`) still land on the 8th and 22nd. That fits a UTC+8 store (WA): 22:00 and 23:47 local.

So `orders` is also wrong. February should be **33 orders / 480.00**, and January should stay at **29 / 496.00**. Fixing only the incremental model would push the shifted dates into the dashboard as well.

A side note: `schema.yml` describes `order_date` as "Date (UTC)". Ops' daily reconciliation shows the values have always been shop-local dates, so that description is wrong.

## 2. Fix

**Data: correct the six backfilled timestamps.**

The proper fix is in the ingestion job. Have the backfill convert to the store's local time, as the regular sync does, and reload. If you need to patch it now, derive the offset from the known order instead of assuming +8:

```sql
-- offset = sync-screen local time minus loaded time for order 100
select timestamp '2018-02-01 04:48:19' - ordered_at as tz_offset
from raw_orders where id = 100;

update raw_orders
set ordered_at = ordered_at + <tz_offset>
where id between 100 and 105;

-- verify: should return 1, 3, 8, 13, 22, 26 February
select id, ordered_at, cast(ordered_at as date) from raw_orders where id between 100 and 105 order by id;
```

**Model: stop the incremental window from missing late data.**

`daily_revenue` is about 70 rows aggregated from about 100 orders, so incremental processing gains nothing here. Make it a plain table:

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

Then run `dbt build --full-refresh -s +daily_revenue`. A full refresh is needed once, even if you keep it incremental. If it must stay incremental, the filter has to be keyed on load time (for example a `_loaded_at` column from ingestion), not on `order_date`. An `order_date` filter will always drop backfills.

**Expected result:** `orders` and `daily_revenue` both show January 29 / 496.00 and February 33 / 480.00.

## 3. Test that would have caught it

A singular test that reconciles the two models per day. Save it as `tests/daily_revenue_matches_orders.sql`:

```sql
with from_orders as (

    select order_date, count(*) as orders, coalesce(sum(amount), 0) as revenue
    from {{ ref('orders') }}
    group by order_date

),

from_daily as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily_revenue
from from_orders o
full outer join from_daily d
    on o.order_date = d.order_date
where o.order_date is null
   or d.order_date is null
   or o.orders != d.orders
   or abs(o.revenue - d.revenue) > 0.005
```

Last night this would have failed on 31 January and on 1, 2, 3, 8, 13, 22 and 26 February.

It does not catch the timezone shift on its own, since both models would agree on the wrong dates. For that, the ingestion load needs a check: either store `ordered_at` as a `timestamptz` with a store/source column, or add a check that compares each store's sync-screen dates (ids, dates, amounts) with `stg_orders`.
