claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 48 s | USD 0.007 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

The bug is in the data, not the SQL. `raw_orders.ordered_at` mixes two clock conventions, and no model converts between them.

- **Admin screen:** shop-local time. The numbers imply UTC+05:30.
- **Warehouse:** `stg_orders` takes `ordered_at` as a naive timestamp and casts it to a date. That date is the one stored in the source, so it is UTC for rows where the source stores UTC.
- **Documentation:** `models/schema.yml` describes `orders.order_date` as "Date (UTC)", but ops reconciles against local dates. The two definitions of "order date" were never reconciled.

The evidence:

| Order | Stored `ordered_at` | Admin shows (+05:30) |
|---|---|---|
| 73 | 2018-03-15 19:45:20 | 2018-03-16 01:15:20 |
| 84 | 2018-03-26 07:04:27 | 2018-03-26 12:34:27 |
| 86 | 2018-03-26 15:45:38 | 2018-03-26 21:15:38 |
| 87 | 2018-03-26 21:12:29 | 2018-03-27 02:42:29 |
| 88 | 2018-03-26 21:51:20 | 2018-03-27 03:21:20 |
| 89 | 2018-03-27 18:45:59 | 2018-03-28 00:15:59 |

Shifting the stored times by +05:30 gives 26 March = 3, 27 March = 2, 28 March = 1. Those are exactly the app's numbers. Shifting by +05:30 also predicts that 23 March should show 4 in the app against 5 here, and 24 March 2 against 1. Your report only listed 26–28, so check those two days too.

The monthly totals match because a 5.5-hour shift moves orders between days but almost never across a month boundary.

The affected files:

- **`seeds/raw_orders.csv`** (the root cause): rows from about 2018-03-15 onward store UTC, and earlier rows store shop-local time. Only the source export can confirm which rows use which convention.
- **`models/staging/stg_orders.sql`**: `cast(ordered_at as date) as order_date` is where the wrong date gets decided. It has no timezone handling at all.
- **`models/schema.yml`**: the `order_date` description says UTC, which conflicts with what ops expects.
- **`models/daily_revenue.sql`**: its incremental window only rebuilds the last 3 days, so the wrong 26–28 March rows are already persisted and won't self-correct.

One caveat: a +05:30 offset is unusual for a shop that reports in AUD, so confirm the zone with the admin screen's timezone setting before hardcoding it.

## 2. Fix

Do these in order. Converting before normalising the data would shift the pre-March rows wrongly.

**a. Confirm the convention and the switch point.** Ask the app team when the export changed, and check a few known orders on each side of 15 March. Then verify the offset:

```sql
select id, ordered_at, cast(ordered_at at time zone 'UTC' at time zone 'Asia/Kolkata' as date) as local_date
from raw_orders
where ordered_at between '2018-03-14' and '2018-03-17'
order by ordered_at;
```

**b. Normalise the data.** Correct the affected `raw_orders` rows so every `ordered_at` uses one convention. Preferably store UTC, since that matches the existing schema description.

**c. Make the conversion explicit in `stg_orders.sql`:**

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is UTC; reporting dates are shop-local
        cast(ordered_at at time zone 'UTC' at time zone 'Asia/Kolkata' as date) as order_date,
        status

    from source

)
```

Update the `order_date` description in `models/schema.yml` to say "shop-local date". Check the DuckDB output of the `AT TIME ZONE` chain on a sample row before trusting it.

**d. Rebuild the persisted history.** The incremental `daily_revenue` won't reach back to March on its own:

```
dbt build --full-refresh -s daily_revenue+
```

## 3. A test that would have caught it

Generic tests (`not_null`, `unique`, `accepted_values`) all pass on wrong data. The problem is internally consistent, so only an external reference can expose it. Export the admin screen's daily order counts to a seed, then compare against it:

`seeds/app_daily_order_counts.csv` with columns `order_date, orders`

`tests/assert_order_counts_match_app.sql`:

```sql
with warehouse as (

    select order_date, count(*) as orders
    from {{ ref('orders') }}
    group by order_date

),

app as (

    select order_date, orders
    from {{ ref('app_daily_order_counts') }}

)

select
    coalesce(w.order_date, a.order_date) as order_date,
    w.orders as warehouse_orders,
    a.orders as app_orders

from warehouse w
full outer join app a
    on w.order_date = a.order_date

where w.orders is distinct from a.orders
```

Against the current data this test fails on 23, 24, 26, 27 and 28 March, which is the correct outcome. Keep the app seed refreshed from the admin screen so the test tracks the figures ops uses.

**Unrelated, but worth checking:** `raw_customer_regions` has an `updated_at` column. If a customer has more than one row, `region_revenue` joins fan out and double-count revenue. Add a `unique` test on `customer_id` in that seed to find out.
