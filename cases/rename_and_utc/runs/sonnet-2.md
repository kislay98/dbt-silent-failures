claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 25 s | USD 0.070 | 2026-10-14 | project f769fdf+wk6ext | prompt 4d613ca19f07

## 1. Root cause

**Data/ingestion change, surfaced in `models/staging/stg_orders.sql`.**

- From order 57 (2018-03-01) onward, the ingestion job leaves `raw_orders.ordered_at` as NULL and writes the timestamp to a new column, `order_ts`. That is the 43 orders (29 + 27 + 43 = 99 orders in total).
- `stg_orders` still computes `cast(ordered_at as date) as order_date`, so those 43 rows get a NULL `order_date`. They drop out of every month bucket in `orders`, and `customers.first_order` and `most_recent_order` go NULL for customers whose orders are all new (9, 12, 93).
- The new column is **UTC, not shop-local**. Order 73 has `order_ts` = 2018-03-15 19:45:20, and the admin screen shows 2018-03-16 01:15:20, which is +05:30. For Jan/Feb the old `ordered_at` matches the admin screen by day, so it was shop-local. For those rows `order_ts` equals `ordered_at` (orders 54–56), so it is only UTC on the new rows. Renaming `ordered_at` to `order_ts` would therefore shift dates by a day for evening orders.
- `daily_revenue` looks fine only because it is stale. Its incremental filter `order_date >= max(order_date) - 3 day` excludes NULL dates, so the March/April rows are left over from earlier runs, and the NULL group never gets inserted.
- Every test passed because nothing tests `order_date`, and `amount` is unaffected (1672.00 both ways).

The +05:30 offset is inferred from order 73 alone, so please confirm the shop's time zone with ops. If it is India, there is no DST, so a fixed offset is safe.

## 2. Fix

`models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ordered_at is shop-local time. Since 2018-03 the feed leaves it NULL and
        -- sends order_ts in UTC, so shift it to shop-local (+05:30)
        coalesce(ordered_at, order_ts + interval '5 hours 30 minutes') as ordered_at,
        cast(coalesce(ordered_at, order_ts + interval '5 hours 30 minutes') as date) as order_date,
        status

    from source

)
```

- Update the `order_date`, `first_order` and `most_recent_order` descriptions in `schema.yml` from "Date (UTC)" to "Date (shop local time)".
- Rebuild `daily_revenue` from scratch, because the incremental run only touches the last three days and the stale March rows use the old dates: `dbt build --full-refresh -s daily_revenue+ stg_orders+`.
- Afterwards, order 73 should land on 2018-03-16, and the reconciliation with the admin screen should agree again.

## 3. Tests

In `models/staging/schema.yml` and `models/schema.yml`, add `not_null` on `stg_orders.order_date` and `orders.order_date`. This alone would have failed the build with 43 failures.

Add a singular test, `tests/daily_revenue_matches_orders.sql`, to catch stale incremental output:

```sql
select o.order_date, o.orders, d.orders as daily_orders
from (select order_date, count(*) as orders from {{ ref('orders') }} group by 1) o
full join {{ ref('daily_revenue') }} d using (order_date)
where o.order_date is distinct from d.order_date
   or o.orders is distinct from d.orders
```

Optionally, add a `dbt_utils.expression_is_true` test on `raw_orders` (`ordered_at is not null or order_ts is not null`) and a not-null check on the new column, so a future schema change in the feed fails loudly.

I haven't edited any files, since the project isn't in my working directory. I can apply these if you point me at the repo.
