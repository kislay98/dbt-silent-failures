claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 33 s | USD 0.007 | 2026-10-15 | project ad0b16b | prompt 11fb6c29d933

## 1. Root cause

**`stg_orders.sql` reads only `ordered_at`, but the raw table now also has `order_ts`, and `ordered_at` is NULL for the newer orders.** The ingestion job appears to have started populating `order_ts` and stopped populating `ordered_at` for orders from about March onward.

- `models/staging/stg_orders.sql` builds `order_date` from `ordered_at` only: `cast(ordered_at as date) as order_date`. The 43 orders with a NULL `order_date` are the 43 raw rows with a NULL `ordered_at` (for example order 63, where `ordered_at` is NULL and `order_ts` is `2018-03-06 12:28:52`).
- Where both columns are populated they are identical (orders 25, 46 and 7 in the sample), so `order_ts` uses the same clock as `ordered_at`, which is the one ops reconciles against.
- `orders` keeps all 99 rows, so `sum(amount)` is still 1672. Only the dates are lost, which is why the monthly report shows 761 in a NULL month.

**Why `daily_revenue` still shows March and April:** it is incremental with `delete+insert`. A run only deletes and re-inserts the `order_date` keys it actually produces, and the NULL-dated orders produce none. The March and April rows are left over from the earlier build. They are exactly the 43 orders and 761.00 of revenue that are now missing from `orders` (35 + 8 = 43; 622 + 139 = 761).

**Why every test passes:**
- `orders.order_date` has no `not_null` test, and neither does `stg_orders`.
- `daily_revenue.not_null` on `order_date` cannot fail, because NULL dates never reach it. The `where order_date >= ...` filter drops them.
- No test compares `orders` with `daily_revenue` or with the source row count.

**Timezone:** `schema.yml` says `order_date` is "Date (UTC)", but ops reconciles against the admin screen in shop-local time and they agreed every day until this load. The existing data therefore behaves as local time. Either the docs are wrong or the column is wrong, and I can't tell which from these files. I could not see `order_ts` for order 73, so please check it directly (see below) before choosing the fix.

## 2. Fix

**Staging (the code fix):** read `order_ts` as the fallback and keep one derived date.

```sql
-- models/staging/stg_orders.sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_orders') }}

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- `ordered_at` is NULL for newer loads; `order_ts` carries the same clock
        coalesce(ordered_at, order_ts) as ordered_at,
        cast(coalesce(ordered_at, order_ts) as date) as order_date,
        status

    from source

)

select * from renamed
```

Before merging, verify the timezone with this query. Order 73 should come back as `2018-03-16 01:15:20`, the same as the admin screen. If `order_ts` is UTC instead, convert it to the shop's local zone before casting to date, and tell me the zone.

```sql
select id, ordered_at, order_ts from raw_orders where id = 73;
```

Also fix the `schema.yml` description ("Date (UTC)") once the timezone is confirmed.

**Upstream:** ask the ingestion owner what happened to `ordered_at`. If it was renamed to `order_ts`, backfill `ordered_at` from it so the staging `coalesce` is no longer needed.

**Rebuild `daily_revenue`:** the 3-day incremental window will not rewrite March and April, so after the fix run:

```
dbt build --full-refresh -s daily_revenue
dbt build -s stg_orders+ --exclude daily_revenue
```

## 3. Test that would have caught it

Add `not_null` on the date columns in the staging schema, and a singular test that reconciles `daily_revenue` with `orders`:

```yaml
# models/staging/schema.yml
  - name: stg_orders
    columns:
      - name: order_id
        ...
      - name: order_date
        tests:
          - not_null
```

```sql
-- tests/assert_daily_revenue_matches_orders.sql
-- every order with a date must be counted in daily_revenue, and vice versa
with live as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    where order_date is not null
    group by order_date
),

built as (
    select order_date, orders, revenue from {{ ref('daily_revenue') }}
)

select coalesce(live.order_date, built.order_date) as order_date
from live
full outer join built on live.order_date = built.order_date
where live.orders is distinct from built.orders
   or abs(coalesce(live.revenue, 0) - coalesce(built.revenue, 0)) > 0.005
```

The `not_null` test on `stg_orders.order_date` fails on the current data (43 rows) and points straight at the staging model. The singular test fails for March and April, where `daily_revenue` has stale totals and `orders` has none. Either one would have flagged this load.
