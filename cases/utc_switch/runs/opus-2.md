claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 54 s | USD 0.183 | 2026-10-14 | project f769fdf | prompt f8d1d1e4d74c

The bug is in the data, not the SQL. Part way through `seeds/raw_orders.csv` (around order 73, mid-March), the `ordered_at` values switch time convention. From that point they are recorded 5 h 30 m behind the shop's local time. `stg_orders.sql` cuts the date off whatever timestamp it is given, so those orders get the wrong day.

## 1. Root cause

**Where:** `seeds/raw_orders.csv`, column `ordered_at` (loaded as a plain `TIMESTAMP` with no timezone). It surfaces in `models/staging/stg_orders.sql`:

```sql
cast(ordered_at as date) as order_date
```

That cast is only correct if every row uses the same clock. Until mid-March the rows were in shop-local time, which is why the counts always matched the admin screen. From about order 73 on, they are not:

- Order 73: admin shows `2018-03-16 01:15:20`, warehouse shows `2018-03-15 19:45:20`. That is exactly −5:30.

Adding 5:30 back to the rows in your sample gives exactly the app's numbers:

| order | warehouse | +5:30 (local) | local date |
|---|---|---|---|
| 84 | 03-26 07:04 | 03-26 12:34 | 26 |
| 85 | 03-26 11:50 | 03-26 17:20 | 26 |
| 86 | 03-26 15:45 | 03-26 21:15 | 26 |
| 87 | 03-26 21:12 | 03-27 02:42 | 27 |
| 88 | 03-26 21:51 | 03-27 03:21 | 27 |
| 89 | 03-27 18:45 | 03-28 00:15 | 28 |

Local counts are 26 → 3, 27 → 2, 28 → 1, matching the app. The warehouse has 5 / 1 / 0.

**Why nothing else noticed:**
- Shifting a time by a few hours only moves orders across day boundaries. No order crosses a month boundary, so monthly totals are unchanged.
- Amounts are untouched, so `sum(amount) = 1672.00` still matches.
- Every test is row-level (`unique`, `not_null`, `accepted_values`). None of them checks what a timestamp means.

**Two side issues:**
- `schema.yml` says `order_date` is "Date (UTC)". Ops reconcile in local time and the dates used to match, so either the docs are wrong or the earlier data was never UTC. Decide which convention you want and document it.
- Order 82 (`03-23 23:44`) should also move, to 24 March. Expect 23 March to be 4 rather than 5 and 24 March to be 2 rather than 1 in the app. If the app agrees, that confirms a uniform shift.

## 2. Fix

**Preferred: fix the data.** Re-export the affected rows from the app in the original local-time convention. Better still, export every row with an explicit UTC offset (e.g. `2018-03-16T01:15:20+10:30`), load it as `timestamptz`, and derive the date in the shop's timezone. Then a convention change cannot go unnoticed again.

**Code fix until the export is corrected:** normalise in staging, keyed on order id rather than on timestamps (which are ambiguous near the switch). First confirm the first affected order by checking a few orders before 73 against the admin screen.

```yaml
# dbt_project.yml
vars:
  # first order exported 5h30m behind shop-local time; confirm against the admin screen
  orders_tz_shift_from_id: 73
```

```sql
-- models/staging/stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        status

    from source

),

-- raw_orders switched timestamp convention mid-March 2018; bring later rows back to shop-local time
normalised as (

    select
        order_id,
        customer_id,
        case
            when order_id >= {{ var('orders_tz_shift_from_id') }}
                then ordered_at + interval '5 hours 30 minutes'
            else ordered_at
        end as ordered_at,
        status

    from renamed

)

select
    order_id,
    customer_id,
    ordered_at,
    cast(ordered_at as date) as order_date,
    status

from normalised
```

Change the `order_date` description to "Date (shop local time)…".

Then rebuild with a full refresh. `daily_revenue` is incremental and only rewrites its last three days, so a normal run would leave the wrong historical dates in place:

```sh
dbt build --full-refresh -s stg_orders+
```

This also corrects `customers.first_order` and `most_recent_order`, which depend on the same dates.

## 3. A test that would have caught it

The only reliable check is reconciling against what ops reconcile against. Have the app export daily counts (or a small set of known reference orders) as a seed, then add a singular test:

```sql
-- tests/assert_daily_order_counts_match_app.sql
-- seeds/app_daily_order_counts.csv: order_date,orders  (exported from the admin screen)
with app as (
    select order_date, orders from {{ ref('app_daily_order_counts') }}
),

warehouse as (
    select order_date, count(*) as orders from {{ ref('orders') }} group by 1
)

select
    coalesce(app.order_date, warehouse.order_date) as order_date,
    app.orders as app_orders,
    warehouse.orders as warehouse_orders
from app
full outer join warehouse on app.order_date = warehouse.order_date
where coalesce(app.orders, 0) <> coalesce(warehouse.orders, 0)
```

Against the current data this fails on 26, 27 and 28 March, and probably 23 and 24 March too.

If an app export isn't available, a cheaper partial check is a seed of a few reference orders with their admin-screen timestamps (order 73 included), plus a test that `stg_orders.ordered_at` matches them exactly. It would have caught this shift, though only for the orders listed.
