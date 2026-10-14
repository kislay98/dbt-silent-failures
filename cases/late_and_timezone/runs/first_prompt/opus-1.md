claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 67 s | USD 0.224 | 2026-10-14 | project 8ee5734 | prompt 0a1f2799e0c1

# Diagnosis: two separate problems with the backfilled orders 100–105

Both problems affect the same six rows. The first explains why `daily_revenue` and `orders` disagree. The second means `orders` is also wrong against the shop admin. So February should be **33 orders / 480.00** in both tables, not 474.00.

## 1. Root cause

### A. `models/daily_revenue.sql`: the incremental window never reaches the backfilled dates

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The newest order is 2018-04-09, so each run only rebuilds 2018-04-06 onward. Orders 100–105 were loaded last night but are dated 31 Jan to 25 Feb. They reached `orders`, which is a full table rebuild. They never reached `daily_revenue`. `delete+insert` only replaces the dates the filter selects, so those days stay frozen at their pre-backfill values.

The mismatch query shows exactly this: one missing order on each of the six dates.

| Date | Missing amount |
|---|---|
| 31 Jan | 6 |
| 2 Feb | 12 |
| 8 Feb | 9 |
| 13 Feb | 15 |
| 22 Feb | 20 |
| 25 Feb | 3 |
| **Total** | **65.00 (6 orders)** |

That is the whole backfill. February's gap is 5 orders and 59.00, and January's is 1 order and 6.00. No test checks the two models against each other, so the build is green.

### B. `raw_orders`: the backfill's `ordered_at` is shifted 5h30m from the shop's local time

The sync screen says the first order was placed at **2018-02-01 04:48:19**. The warehouse holds **2018-01-31 23:18:19**. Every backfilled row is offset by the same 5h30m:

| order | warehouse `ordered_at` | +5:30 | sync screen |
|---|---|---|---|
| 100 | 01-31 23:18:19 | **02-01** 04:48 | 1 Feb |
| 101 | 02-02 20:42:44 | **02-03** 02:12 | 3 Feb |
| 102 | 02-08 14:00:05 | 02-08 19:30 | 8 Feb |
| 103 | 02-13 07:35:51 | 02-13 13:05 | 13 Feb |
| 104 | 02-22 15:47:33 | 02-22 21:17 | 22 Feb |
| 105 | 02-25 19:29:02 | **02-26** 00:59 | 26 Feb |

The backfill was exported in a different timezone convention from the shop's local time. Because of this, three orders land on the wrong `order_date` in `stg_orders` and `orders`, and order 100 falls into January. The other three only change time of day, not date.

`raw_orders.ordered_at` is a plain `TIMESTAMP` with no zone, so nothing downstream can detect the shift. The sync screen puts all six orders (65.00) in February. So the correct figures are:

| Month | Orders | Revenue |
|---|---|---|
| Jan | 29 | 496.00 |
| Feb | 33 | 480.00 (415 + 65) |

One caveat: `schema.yml` documents `order_date` as "Date (UTC)". That contradicts ops reconciling successfully against local time. Before applying the shift, check one *non-backfilled* order from this store against the shop admin to confirm the warehouse convention is shop-local. If that check holds, the docs are wrong too.

## 2. Fix

**Data, for B.** Have the ingestion job re-deliver orders 100–105 in the same convention as the rest of `raw_orders`. Fix it at the source so the next load doesn't overwrite a manual patch. Until then, the equivalent correction is:

```sql
update raw_orders
set ordered_at = ordered_at + interval '5 hours 30 minutes'
where id between 100 and 105;
```

**Code, for A.** The table is tiny and the incremental window can't see late-arriving history, so make it a plain table. In `models/daily_revenue.sql`:

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

If it has to stay incremental, there is another option. Have ingestion add a `_loaded_at` column to `raw_orders`, carry it through `stg_orders` and `orders`, and rebuild every `order_date` touched by rows loaded since the last run, instead of the last three order dates.

**Then rebuild:**

```
dbt build --select stg_orders+ --full-refresh
```

`--full-refresh` repairs the frozen days if you keep the model incremental. `customers` also picks up the corrected `first_order` and `most_recent_order` for customers 2, 41 and 77.

## 3. Test that would have caught it

Add `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must agree with orders for every order_date
with orders_by_day as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

),

daily as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily_revenue

from orders_by_day o
full outer join daily d
    on o.order_date = d.order_date

where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Last night this test would have failed with the six rows ops found by hand.

It cannot catch problem B, because both models inherit the same wrong dates. That needs a contract at ingestion: land `ordered_at` as `TIMESTAMPTZ` or with an explicit source timezone, then normalise it in `stg_orders`. Alternatively, reconcile against the store sync manifest if it can be loaded.

Also note that the build ran 28 of the 32 data tests. The four seed tests, on `raw_customer_regions` and `raw_payment_methods`, were excluded together with the seeds, so nothing checks those inputs.
