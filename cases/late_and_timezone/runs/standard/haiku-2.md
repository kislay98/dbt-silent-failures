claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 66 s | USD 0.010 | 2026-10-14 | project ad0b16b | prompt 150d20052204

## 1. Root cause

The mismatch comes from `jaffle_shop/models/daily_revenue.sql`. It is incremental and only rebuilds the last three days of `order_date`:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The backfill rows are dated in February, well outside that window, so no run after the load ever touches them. `orders` is a full table rebuild, so it picks them up. `daily_revenue` never does. The dashboard has been silently stale for those dates since the load. The two tables matched before only because no late data had arrived.

The numbers match this exactly, and the Jan/Feb split is worth noting:

| Month | `orders` | `daily_revenue` | Difference |
|---|---|---|---|
| Jan | 30 / 502.00 | 29 / 496.00 | 1 order / 6.00 |
| Feb | 32 / 474.00 | 27 / 415.00 | 5 orders / 59.00 |
| **Total** | | | **6 orders / 65.00** |

The six backfilled orders are worth 65.00, so the two gaps together are the whole backfill. One of them is in January in `orders`, not February. My inference, which I haven't verified against the rows, is that `stg_orders.sql` derives `order_date` from UTC (`cast(ordered_at as date)`, documented as "Date (UTC)" in `models/schema.yml`). The first backfilled order shown in the shop admin as 2018-02-01 04:48:19 local would be 2018-01-31 in UTC for a UTC+11 shop, so it lands in January. That is the single order in the January gap.

The data problem is that the backfill contains orders dated more than three days before the last load, and that `ordered_at` is stored in UTC while ops reconciles in local time. The first problem is the one that breaks the dashboard. The second decides which day the 04:48 order falls on.

Caveat: I can't confirm the timezone explanation from the sample. Run this to check the order directly:

```sql
select order_id, ordered_at, cast(ordered_at as date) as order_date, amount
from orders
where ordered_at between '2018-01-30' and '2018-02-02'
order by ordered_at;
```

Also, it's surprising that the UTC/local difference hasn't caused disagreements before. If the shop is UTC+11, orders placed after 13:00 UTC would already fall on a different local date. Ops should confirm which timezone the admin uses before anyone changes the date logic.

## 2. Fix

**Required: `daily_revenue` should not be incremental.** The 3-day comment is also wrong. Payments don't cause the lag here, because `orders` is already a full table rebuild. The window neither catches late orders nor refreshes old amounts. The table is 69 rows, so a full rebuild is cheap:

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

Then run `dbt build --select daily_revenue` once to correct the existing rows. If you want to keep it incremental, run `dbt run --select daily_revenue --full-refresh` after every late load, but the table is simpler.

**Needs your decision: the date basis in `stg_orders.sql`.** To make the January order move to February, `order_date` would need to be the shop's local date. For example:

```sql
cast(ordered_at at time zone 'UTC' at time zone 'Australia/Sydney' as date) as order_date
```

I haven't made this change. It moves historical rows across days, changes the meaning of the column, and the zone is a guess that needs confirming with ops. If you adopt it, update the `order_date` description in `models/schema.yml` as well.

I haven't changed any files.

## 3. A test that would have caught this

The existing tests check each table in isolation (unique, not null), so nothing compares `daily_revenue` with `orders`. Add a singular test at `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with expected as (

    select
        order_date,
        count(*) as orders,
        round(sum(amount), 2) as revenue

    from {{ ref('orders') }}

    group by order_date

),

actual as (

    select
        order_date,
        orders,
        round(revenue, 2) as revenue

    from {{ ref('daily_revenue') }}

)

select
    coalesce(expected.order_date, actual.order_date) as order_date

from expected

full outer join actual
    using (order_date)

where expected.orders is distinct from actual.orders
   or expected.revenue is distinct from actual.revenue
```

This fails for any date where the two tables disagree, including dates present in only one of them. It would have failed on the first build after the backfill, on 2018-01-31 and 2018-02 dates, which is when it was needed.
