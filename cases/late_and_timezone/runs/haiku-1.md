claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 66 s | USD 0.009 | 2026-10-14 | project 8ee5734 | prompt d3281e93889c

## 1. Root cause

**The dashboard is stale, not wrong.** `orders` is rebuilt in full on every run, but `daily_revenue` is incremental and only rebuilds the last three days of `order_date`. The backfill contains orders 100–105, which are dated 31 Jan through 25 Feb. Those dates fall outside the window, so `delete+insert` never touches them and the pre-backfill rows stay in `daily_revenue`.

- **`jaffle_shop/models/daily_revenue.sql`** (the `is_incremental()` filter): the 3-day lookback assumes late data is at most a few days old. The comment ("payments can land a day or two after the order") covers late payments, not late orders. This backfill delivered whole orders weeks late.
- **Data:** `raw_orders` has six completed orders that arrived in the last load. Their `order_date` (`stg_orders.sql`, `cast(ordered_at as date)`, UTC) is:

| order_id | ordered_at (UTC) | order_date (UTC) | shop local (+05:30) | amount |
|---|---|---|---|---|
| 100 | 2018-01-31 23:18:19 | 2018-01-31 | 2018-02-01 04:48:19 | 6.00 |
| 101 | 2018-02-02 20:42:44 | 2018-02-02 | 2018-02-03 02:12:44 | 12.00 |
| 102 | 2018-02-08 14:00:05 | 2018-02-08 | 2018-02-08 19:30:05 | 9.00 |
| 103 | 2018-02-13 07:35:51 | 2018-02-13 | 2018-02-13 13:05:51 | 15.00 |
| 104 | 2018-02-22 15:47:33 | 2018-02-22 | 2018-02-22 21:17:33 | 20.00 |
| 105 | 2018-02-25 19:29:02 | 2018-02-25 | 2018-02-26 00:59:02 | 3.00 |

The output confirms this. Each mismatched date differs by exactly one backfilled order, and the differences sum to 65.00. February is 5 orders and 59.00 (474.00 − 415.00, 32 − 27). January is 1 order and 6.00 (502.00 − 496.00, 30 − 29).

**Why ops expects six Feb orders and the warehouse shows five:** the shop is at UTC+05:30 (order 100 is 23:18:19 UTC and 04:48:19 local). Order 100 is 31 January in the warehouse, which is UTC, but 1 February in the shop admin. `order_date` is documented as UTC in `schema.yml`, so the models are correct as documented. The reconciliation is comparing two timezones. Ops should also check which timezone their per-day reconciliation really uses. At +05:30, orders placed after 18:30 UTC would already have shown different dates from the admin on normal days, which doesn't fit "agreed every day since January."

The existing tests cannot catch this. Each one checks a single column of a single table (not null, unique, accepted values), and a stale table can pass all of them.

I worked this out from the log and sample output. I have not run `dbt` against the warehouse.

## 2. Fix

**a) Make `daily_revenue` a full rebuild.** The table is small and the project already defaults models to `table`, so the incremental config is not worth keeping. Replace `jaffle_shop/models/daily_revenue.sql` with:

```sql
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

Then run `dbt build --select daily_revenue` once. The full rebuild repairs the existing rows, and every later run stays correct. If you want to keep it incremental, you must run `dbt run --select daily_revenue --full-refresh` to repair it, and any lookback you pick will still miss the next backfill older than that window.

Also update the description in `schema.yml`, which currently says "rebuilds the last three days on each run."

**b) Timezone (a decision for ops, not a code bug).** Keep `order_date` in UTC, since that is what the docs promise, and have ops reconcile in UTC. If the business reports in shop-local dates, add a separate column rather than changing `order_date`, for example `cast(ordered_at + interval 5 hour 30 minute as date) as order_date_local` in `stg_orders.sql`. Confirm the offset with the shop before doing this.

**Expected result after (a):** `daily_revenue` matches `orders` exactly. February is 32 orders and 474.00 and January is 30 orders and 502.00, both in UTC.

## 3. Test that would have caught it

Add a singular test at `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`. It returns a row for every date where the two tables disagree, so any row fails the build:

```sql
with from_orders as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily_revenue

from from_orders o

full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date

where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

This would have failed the nightly build: 2018-01-31, 02-02, 02-08, 02-13, 02-22 and 02-25 would each have returned a row. It also catches any future drift between the two models, whatever causes it. The other fixes are less general: the 3-day window only covers this one case, and removing the incremental config only covers this model.
