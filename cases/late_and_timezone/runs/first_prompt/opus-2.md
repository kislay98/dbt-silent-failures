claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 65 s | USD 0.191 | 2026-10-14 | project 8ee5734 | prompt 0a1f2799e0c1

# daily_revenue vs. orders: February mismatch from late backfill and shifted timestamps

## 1. Root cause

There are two separate problems. Both involve the six backfilled orders, ids 100–105.

**A. `daily_revenue` never picked up the backfill (`models/daily_revenue.sql`)**

The incremental filter only rebuilds dates within three days of the newest date already in the table:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`max(order_date)` is 2018-04-09, so last night's run only rebuilt 2018-04-06 onward. The backfilled orders are dated January and February, so they reached `orders` (a full table rebuild) but were never merged into `daily_revenue`. The diff query confirms this: the gaps are exactly orders 100–105, worth 6+12+9+15+20+3 = 65.00. The tests passed because nothing compares the two models.

**B. The backfill's timestamps are shifted 5h30m earlier (`raw_orders`, rows id 100–105)**

The store's sync screen shows the first order at **2018-02-01 04:48:19**. `raw_orders` has it at **2018-01-31 23:18:19**, which is exactly −5:30. Applying that offset to every backfill row reproduces the sync screen's dates exactly:

| id | raw `ordered_at` | +5:30 | sync screen |
|---|---|---|---|
| 100 | 01-31 23:18:19 | 02-01 04:48 | 1 Feb |
| 101 | 02-02 20:42:44 | 02-03 02:12 | 3 Feb |
| 102 | 02-08 14:00:05 | 02-08 19:30 | 8 Feb |
| 103 | 02-13 07:35:51 | 02-13 13:05 | 13 Feb |
| 104 | 02-22 15:47:33 | 02-22 21:17 | 22 Feb |
| 105 | 02-25 19:29:02 | 02-26 00:59 | 26 Feb |

The rest of `raw_orders` evidently holds shop-local times, because `orders.order_date` has always reconciled against the admin. The backfill path converted these six rows to UTC; the regular sync doesn't do that. As a result, order 100 lands in January and 101 and 105 land on the wrong day, even in `orders`.

So **neither** table is currently right. `orders` shows 32 / 474.00 for February, but the correct figure is **33 / 480.00**. January should be **29 / 496.00**.

Side note: `schema.yml` documents `order_date` as "Date (UTC)". The data is actually shop-local, so the docs are wrong too.

## 2. Fix

**Data.** Fix the backfill at its source: the ingestion job's backfill path, or `seeds/raw_orders.csv` if that's where it lives. That way the next load or `dbt seed` doesn't reintroduce the shift. It should emit the same local timestamps as the regular sync. To correct the rows already loaded in the meantime:

```sql
update raw_orders
set ordered_at = ordered_at + interval '5 hours 30 minutes'
where id between 100 and 105;
```

**Model.** The table is tiny, and late-arriving data has no load timestamp to filter on, so drop the incremental logic in `models/daily_revenue.sql`:

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

If it must stay incremental, at least run `dbt build -s daily_revenue --full-refresh` after every backfill. Without that, the lookback window will silently miss any late data again.

After the data fix, rebuild with `dbt build -s stg_orders+`. Then both models should show January 29 / 496.00 and February 33 / 480.00.

## 3. Test that would have caught it

Add `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with orders_by_day as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select *
from orders_by_day o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

This would have failed last night's build on the six dates in your diff.

It will **not** catch problem B. Nothing in the warehouse records the correct local time, so a shifted timestamp looks valid. Two options would catch it:

- An ingestion-side check comparing per-store, per-day counts against the store's sync report.
- Storing `ordered_at` with its offset (`timestamptz`), so the backfill's conversion would have been visible.
