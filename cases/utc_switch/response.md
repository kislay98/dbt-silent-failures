## 1. Root cause

The bug is in the raw data, not in the SQL. `seeds/raw_orders.csv` (loaded as `raw_orders.ordered_at`, a naive `TIMESTAMP`) mixes two time zones:

- Older orders are stored in shop-local time, which is IST (UTC+5:30, no DST).
- From about order 73 (mid-March) onward, `ordered_at` is stored in UTC. Order 73 is 2018-03-15 19:45:20 here and 2018-03-16 01:15:20 in the app, a difference of exactly 5h30m.

`models/staging/stg_orders.sql` then does `cast(ordered_at as date) as order_date`, which assumes the column is local time. For the UTC-era rows, any order placed between 00:00 and 05:30 local lands on the previous calendar day.

I checked this against your numbers by adding 5:30 to the sample rows:

| Date | Orders from the sample, after +5:30 | App |
|---|---|---|
| 26 Mar | 84, 85, 86 | 3 |
| 27 Mar | 87, 88 (02:42, 03:21) | 2 |
| 28 Mar | 89 (00:15) | 1 |

That matches the app's 3/2/1.

Nothing flagged it, for these reasons:

- The shifts only move orders across midnight, so row counts, `sum(amount)` (1672.00) and the monthly totals stay the same.
- Every test checks nulls, uniqueness or accepted values, and none checks the date semantics.
- `schema.yml` documents `order_date` as "Date (UTC)", which hides the mismatch.

I haven't run anything. This is based on the files and output you pasted, and the exact boundary id needs confirming (see below).

## 2. Fix

Normalise the timestamps in staging. Ideally the loader is fixed to emit one zone, but until then:

```sql
-- models/staging/stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- raw_orders switched from shop-local time to UTC at order 73 (mid-March 2018).
        -- Normalise everything to shop-local time (IST, UTC+5:30).
        case
            when id >= 73 then ordered_at + interval 5 hour + interval 30 minute
            else ordered_at
        end as ordered_at,
        status

    from source

),

with_date as (

    select *, cast(ordered_at as date) as order_date
    from renamed

)

select * from with_date
```

Before merging, confirm the boundary: check order 72 and its neighbours against the admin screen. Use the id as the cutoff, not a timestamp, because the timestamps are ambiguous around the switch.

Then:

- Rebuild `daily_revenue` with `dbt build --full-refresh -s daily_revenue+`. It is incremental and only reprocesses the last 3 days, so the wrong historical rows would otherwise stay.
- Update the `order_date` and `first_order` descriptions in `schema.yml` from "Date (UTC)" to "Date (shop local time, IST)".
- Ask whoever owns the feed to make `ordered_at` consistent, ideally a `timestamptz`, and then delete the case expression.

## 3. Test that would have caught it

Reconcile against the number ops already uses. Add a seed `seeds/app_daily_orders.csv` with `order_date,orders`, exported from the admin screen, and a singular test:

```sql
-- tests/assert_daily_orders_match_app.sql
with ours as (
    select order_date, count(*) as orders
    from {{ ref('orders') }}
    group by 1
)

select
    coalesce(o.order_date, a.order_date) as order_date,
    o.orders as dbt_orders,
    a.orders as app_orders
from ours o
full outer join {{ ref('app_daily_orders') }} a
    on o.order_date = a.order_date
where coalesce(o.orders, 0) != coalesce(a.orders, 0)
```

This would have failed on 26, 27 and 28 March. A cheap generic guard as well is an hour-of-day distribution check on `stg_orders` that alerts if the share of orders between 00:00 and 05:30 changes sharply. A switch in the zone convention shows up as a step change in that share.
