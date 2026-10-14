claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 58 s | USD 0.008 | 2026-10-14 | project f769fdf+wk6ext | prompt 4d613ca19f07

## 1. Root cause

**`raw_orders` changed shape in the ingestion load, and `stg_orders.sql` reads only the column that is now empty.**

- **`models/staging/stg_orders.sql`** builds `order_date` from `ordered_at` alone: `cast(ordered_at as date)`. Ids 54–56 have both `ordered_at` and `order_ts` populated and equal. From id 57 onward, `ordered_at` is NULL and the timestamp is only in `order_ts`. The ingestion job appears to have moved new orders to `order_ts`.
- **The 43 NULL `order_date` rows are exactly the March and April orders.** Daily_revenue's stale March (35 orders, $622) and April (8 orders, $139) add up to 43 orders and $761, which is the NULL row in the orders report. The total of $1672 is unchanged because the amounts are still there. Only the dates are gone.
- **`order_ts` is UTC, and `ordered_at` was shop-local.** Order 73 has `order_ts` 2018-03-15 19:45:20 and the admin screen shows 2018-03-16 01:15:20. The +05:30 offset points to a zone such as Asia/Kolkata or Asia/Colombo. I can't name the zone from the data, so confirm it with ops.
- **Downstream effects:** `customers` now has NULL `first_order` for customers 9, 12 and 93, who each have one order. `customers.sql` takes `min(order_date)`, so NULL dates produce NULL first orders.
- **Why `daily_revenue` still shows March and April:** it is incremental with `delete+insert` on `order_date`. The run only deletes keys that appear in the new batch. The NULL-date orders produce no batch rows, so the old March and April rows are never deleted. The delete logic cannot remove a day that disappears from the source.
- **Why every test passed:** no test checks `order_date` in `stg_orders` or `orders`. The `not_null` on `daily_revenue.order_date` passes because the incremental filter `order_date >= …` drops NULLs before the test runs.

## 2. Fix

**`models/staging/stg_orders.sql`**: use `ordered_at` when present, otherwise convert the UTC `order_ts` to shop-local time. Replace the file body with:

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_orders') }}

),

localised as (

    select
        *,
        -- ordered_at is shop-local time. From id 57 the feed only fills order_ts, which is UTC
        coalesce(
            ordered_at,
            timezone('Asia/Kolkata', timezone('UTC', order_ts))
        ) as ordered_local_at

    from source

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_local_at as ordered_at,
        cast(ordered_local_at as date) as order_date,
        status

    from localised

)

select * from renamed
```

Replace `'Asia/Kolkata'` with the shop's zone once ops confirms it. Before running the build, check in DuckDB that order 73 converts to `2018-03-16`. If it doesn't, the `timezone()` argument order is the first thing to recheck.

**Rebuild `daily_revenue` with a full refresh.** A normal run will not remove the stale March and April rows:

```
dbt build --select stg_orders+ --full-refresh
```

**Upstream:** ask the ingestion owners to fill `ordered_at` in shop-local time, or to document that `order_ts` is UTC. The `coalesce` will keep working if `ordered_at` is filled again later.

**Docs:** `schema.yml` describes `order_date` as "Date (UTC)". After this change it is the shop-local date, so update the description.

## 3. Test that would have caught it

Add `not_null` to `order_date` in `models/staging/schema.yml`, and to the same column in `models/schema.yml`:

```yaml
  - name: stg_orders
    columns:
      - name: order_date
        tests:
          - not_null
```

```yaml
  - name: orders
    columns:
      - name: order_date
        description: Shop-local date the order was placed, from the ordered_at timestamp
        tests:
          - not_null
```

With this in place, the 43 NULL dates fail the build with ERROR rather than the nightly reporting PASS=35.

Optionally, add a regression check for the timezone in `tests/order_73_local_date.sql`:

```sql
select order_id, order_date
from {{ ref('orders') }}
where order_id = 73
  and order_date != date '2018-03-16'
```

I have not run any of these queries or the build. The fix and the timezone conversion should be verified against the warehouse before the nightly run.
