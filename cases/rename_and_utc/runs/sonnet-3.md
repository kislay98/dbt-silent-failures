claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 34 s | USD 0.083 | 2026-10-14 | project f769fdf+wk6ext | prompt 4d613ca19f07

## 1. Root cause

**The data:** the ingestion job changed the `raw_orders` schema. It added an `order_ts` column and stopped filling `ordered_at` for new rows. Orders 57 onward (the 43 with no `order_date`) have `ordered_at = NULL` and a populated `order_ts`.

**The code:** `models/staging/stg_orders.sql` still derives the date from the old column, `cast(ordered_at as date) as order_date`. So `order_date` is NULL for those 43 orders, and they show up as the NULL month in the `orders` report. The same NULLs flow into `customers.first_order` and `most_recent_order`.

**Why the build was green:**
- `orders.order_date` and `stg_orders.order_date` have no `not_null` test.
- `sum(amount)` is unaffected, because the NULL-date rows still join to payments. That is why it is 1672.00 in both tables.
- `daily_revenue` only looks right because it is incremental. Its filter `where order_date >= max(order_date) - 3 days` silently drops NULL-date rows, and the March and April rows are left over from earlier loads. A full refresh would have produced a NULL `order_date` group and failed its `not_null` test.

**Timezone trap:** don't just swap in `cast(order_ts as date)`.
- Old `ordered_at` values are shop-local time, since they matched the admin screen every day.
- The new `order_ts` values are UTC. Order 73 is 2018-03-15 19:45:20 in `order_ts` and 2018-03-16 01:15:20 on the admin screen, an offset of +05:30.
- Order 73 would land on the wrong day, and so would any order whose UTC time is 18:30 or later.
- For old rows, `order_ts` equals `ordered_at`, so those rows must not be shifted. Only convert when `ordered_at` is NULL.

The +05:30 offset points to `Asia/Kolkata`, but I inferred that from one order. Please confirm the shop's timezone with ops.

## 2. Fix

`models/staging/stg_orders.sql`:

```sql
{% set shop_tz = var('shop_timezone', 'Asia/Kolkata') %}

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ordered_at is shop-local; order_ts (new feed) is UTC, so convert it to shop-local
        coalesce(
            ordered_at,
            (order_ts at time zone 'UTC') at time zone '{{ shop_tz }}'
        ) as ordered_at,
        cast(
            coalesce(
                ordered_at,
                (order_ts at time zone 'UTC') at time zone '{{ shop_tz }}'
            ) as date
        ) as order_date,
        status

    from source

)
```

The inline version above avoids restructuring the CTE. If you prefer, compute `ordered_at` in one CTE and cast it in the next. This needs DuckDB's ICU extension for `AT TIME ZONE`. If it isn't available, `order_ts + interval 330 minute` gives the same result for a fixed offset.

Also change the `order_date` descriptions in `schema.yml` from "Date (UTC)" to "Date in shop-local time". The same goes for `first_order` and `most_recent_order`.

Then rebuild, including `daily_revenue` with a full refresh so nothing stale remains:

```
dbt build --full-refresh -s stg_orders+
```

Expected after the fix:
- Monthly `orders`: 2018-01 29 / 496, 2018-02 27 / 415, 2018-03 35 / 622, 2018-04 8 / 139, and no NULL row.
- Order 73 has `order_date = 2018-03-16`.
- `daily_revenue` matches what it showed before.

Separately, ask the ingestion owners to stop dropping `ordered_at` or to document the move to UTC `order_ts`. Otherwise this will recur.

## 3. Tests that would have caught it

In `models/schema.yml`, add `not_null` to `orders.order_date`, and also to `stg_orders.order_date` in `models/staging/schema.yml`:

```yaml
      - name: order_date
        description: Date (shop-local) that the order was placed
        tests:
          - not_null
```

That alone would have failed the build with 43 failures. Add a reconciliation test, `tests/assert_daily_revenue_matches_orders.sql`, to catch a stale incremental table:

```sql
-- every order date must agree between orders and daily_revenue
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)
select *
from o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date is not distinct from d.order_date
where o.order_date is distinct from d.order_date
   or o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

Optionally, add a staging guard against the next schema drift:

```sql
-- tests/assert_raw_orders_have_a_timestamp.sql
select id from {{ ref('raw_orders') }}
where ordered_at is null and order_ts is null
```

I haven't run any of this, since the project files aren't in my working directory. The expected numbers come from the outputs you posted.
