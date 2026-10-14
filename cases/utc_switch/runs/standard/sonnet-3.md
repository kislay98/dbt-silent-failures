claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 35 s | USD 0.108 | 2026-10-15 | project ad0b16b | prompt 63b47184243a

## 1. Root cause

I couldn't see the seed data, so this is inferred from your numbers and not checked against the rows.

The fault is in `seeds/raw_orders.csv` (loaded as `raw_orders`), consumed by `models/staging/stg_orders.sql`. The `ordered_at` column mixes two meanings of time:

- **Before mid-March**, `ordered_at` was the shop's local wall-clock time. That is why `cast(ordered_at as date)` matched the admin screen every morning.
- **From roughly order 73 onward**, `ordered_at` is UTC. The column is a naive `TIMESTAMP` with no zone, so nothing flags the change. Order 73 shows 19:45:20 here and 01:15:20 in the app, a 5h30 offset, which is what pushes it from 15 March to 16 March.

`stg_orders.sql` does `cast(ordered_at as date) as order_date` with no timezone handling, so every post-cutover order placed in the late-evening UTC window lands one day early. That matches your numbers. Orders move from 27 and 28 March back to 26 March (5 vs 3, 1 vs 2, 0 vs 1), and monthly totals and `sum(amount)` still match because no rows are lost or duplicated.

Every dbt test passes because none of them looks at date semantics. They only check uniqueness, nulls, accepted values and relationships.

`schema.yml` also describes `order_date` as "Date (UTC)", which is the opposite of what ops reconciles against.

## 2. Fix

Convert the UTC-era rows to shop-local time in staging. Use the zone ops confirms. +5:30 with no DST fits `Asia/Kolkata`, but the AUD amounts and Australian regions suggest I should not assume it. Also confirm the exact first UTC-era order with ops. Order 73 is the first one I know is wrong, and an earlier one may be too.

```sql
-- models/staging/stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- orders from the cutover id onward are loaded in UTC; earlier ones are already shop-local
        case
            when id >= {{ var('utc_orders_from_id', 73) }}
                then (ordered_at at time zone 'UTC') at time zone '{{ var("shop_timezone", "Asia/Kolkata") }}'
            else ordered_at
        end as ordered_at,
        cast(
            case
                when id >= {{ var('utc_orders_from_id', 73) }}
                    then (ordered_at at time zone 'UTC') at time zone '{{ var("shop_timezone", "Asia/Kolkata") }}'
                else ordered_at
            end as date
        ) as order_date,
        status

    from source

)
```

Cleaner long-term: fix the loader so the feed has one meaning, and make the column `TIMESTAMPTZ` or UTC throughout. After that, drop the `case` and convert once.

Then update the `order_date` and `first_order`/`most_recent_order` descriptions in `schema.yml` from "(UTC)" to "shop local date".

`daily_revenue` is incremental and only rebuilds the last 3 days, so the wrong March rows would stay wrong. Run:

```
dbt build --full-refresh -s stg_orders+ 
```

or at least `dbt run --full-refresh -s daily_revenue` after the other models are rebuilt.

## 3. Test that would have caught it

Add a reconciliation seed with the admin screen's daily counts (`seeds/app_daily_orders.csv`, columns `order_date,orders`), which ops already produces each morning. Then add a singular test:

```sql
-- tests/assert_daily_orders_match_app.sql
select
    coalesce(d.order_date, a.order_date) as order_date,
    d.orders as warehouse_orders,
    a.orders as app_orders
from {{ ref('daily_revenue') }} d
full outer join {{ ref('app_daily_orders') }} a
    on d.order_date = a.order_date
where coalesce(d.orders, 0) <> coalesce(a.orders, 0)
```

This would have failed on 26, 27 and 28 March. A cheaper guard is a regression test on a known row, such as order 73 having `order_date = '2018-03-16'`. For the underlying drift, a source freshness or distribution check on the hour-of-day of `ordered_at` would show the feed shifting by 5h30 at the cutover.
