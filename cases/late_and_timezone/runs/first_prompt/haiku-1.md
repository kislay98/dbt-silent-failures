claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 44 s | USD 0.007 | 2026-10-14 | project 8ee5734 | prompt 0a1f2799e0c1

## 1. Root cause

**`models/daily_revenue.sql` is stale for February, because its incremental filter never reprocesses old dates.**

On each run, the incremental branch only rebuilds rows where `order_date >= max(order_date) - 3 days` from the existing `daily_revenue` table. The table's max date is in April, so the window covers early April onward. The backfilled orders are dated late January and February, so the `delete+insert` never touches them. `orders` is a full-refresh table and picks them up. `daily_revenue` keeps its pre-backfill February numbers. The model comment ("payments can land a day or two after the order") assumes late data is recent. A backfill is weeks late, so the assumption fails.

The raw data is not wrong. The six orders are in `orders` on the dates the warehouse stores them, and the arithmetic confirms they are the only difference. The per-date gaps from the mismatch query are:

| order_date (UTC) | extra orders | extra amount |
|---|---|---|
| 2018-01-31 | 1 | 6 |
| 2018-02-02 | 1 | 12 |
| 2018-02-08 | 1 | 9 |
| 2018-02-13 | 1 | 15 |
| 2018-02-22 | 1 | 20 |
| 2018-02-25 | 1 | 3 |

That is 6 orders and 65.00 in total, which matches the backfill. February alone accounts for 5 orders and 59.00 (474 − 415, 32 − 27). The sixth order is the 1 February local-time order, which is dated 31 January in the warehouse.

**Timezone is a separate, expected discrepancy.** `ordered_at` is UTC, and `order_date` is its UTC date. The shop admin shows local time. The sync screen's 1 Feb 04:48:19 local time is 31 Jan in UTC, so the local offset is more than 4h48m ahead of UTC. The 1, 3 and 26 February orders land on 31 Jan, 2 Feb and 25 Feb in UTC. This explains why ops sees different dates, but it does not explain the `orders` vs `daily_revenue` mismatch. That mismatch is the staleness above.

**Why tests passed:** `unique` and `not_null` only check `daily_revenue` on its own. Nothing compares it to `orders`.

## 2. Fix

**Make `daily_revenue` a full-refresh table.** `orders` is already rebuilt every run, and this aggregation is cheap, so the incremental logic adds risk without benefit:

```sql
-- models/daily_revenue.sql
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

Then rebuild it once: `dbt run -s daily_revenue`. Because it is derived from `orders`, nothing is lost. Check the dashboard afterwards against `orders`.

**Timezone (decision for ops, not changed here).** I would not silently change `order_date`, since the schema docs define it as UTC. If ops needs local dates, add a separate `order_date_local` column in `stg_orders` or `orders`, converted from UTC to the shop's zone. The zone is probably Australia/Sydney, given AUD and the offset, but confirm it with ops. Then reconcile on that column.

## 3. Test that would have caught it

Add a singular test that compares every day in `daily_revenue` with `orders`, not just the last three days. It would have failed on the first run after the backfill, on 31 January:

```sql
-- tests/assert_daily_revenue_matches_orders.sql
with orders_by_day as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

),

compared as (

    select
        coalesce(o.order_date, d.order_date) as order_date,
        coalesce(o.orders, 0) as orders_expected,
        coalesce(d.orders, 0) as orders_actual,
        coalesce(o.revenue, 0) as revenue_expected,
        coalesce(d.revenue, 0) as revenue_actual

    from orders_by_day o

    full outer join {{ ref('daily_revenue') }} d
        on o.order_date = d.order_date

)

select *
from compared
where orders_expected <> orders_actual
   or abs(revenue_expected - revenue_actual) > 0.005
```

I have not run these changes. The analysis is based on the log, schema, and query output you provided. I can apply the model change and the test, then run `dbt build` to confirm the test fails on the current stale table before the fix and passes after it.
