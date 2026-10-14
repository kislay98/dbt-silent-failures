claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 36 s | USD 0.085 | 2026-10-14 | project f769fdf+wk6ext | prompt c5676332c543

## 1. Root cause

**The ingestion job changed the `raw_orders` feed, and `models/staging/stg_orders.sql` still reads the old column.**

From 2018-03-01 on (orders 57 onward, 43 rows), `raw_orders.ordered_at` is NULL and the timestamp arrives in a new column, `order_ts`. For older rows both columns hold the same value (orders 54-56).

`stg_orders.sql` computes `cast(ordered_at as date) as order_date`, so those 43 orders get a NULL `order_date`. That produces:

- **`orders`:** the monthly report drops March and April and shows a NULL bucket (43 orders, 761.0). The sum of `amount` is unchanged at 1672.00 because the amounts still join on `order_id`.
- **`customers`:** `first_order` and `most_recent_order` are NULL for customers 9, 12, 93 and others.
- **`daily_revenue`:** it still looks right only because it is incremental and rebuilds just the last 3 days of its own `max(order_date)`. The March and April rows are stale leftovers from earlier runs. The `where order_date >= ...` filter also silently excludes the NULL-date orders. A `--full-refresh` would produce a NULL-date row and fail its `not_null` test.
- **Tests:** nothing tests `order_date`. It has no `not_null` test in either schema file, so everything passed.

**Second problem: timezone.** Order 73 has `order_ts` 2018-03-15 19:45:20, but the admin screen shows 2018-03-16 01:15:20. That is a +5:30 offset, so the raw timestamps are UTC and the shop is on IST (Asia/Kolkata). `order_date` is documented as "Date (UTC)", but ops reconciles against local time.

A plain `coalesce(ordered_at, order_ts)` would bring March and April back, but any order placed after 18:30 UTC would land on the wrong day. Orders 54-56 show `ordered_at` = `order_ts`, so the Jan/Feb dates are UTC too and have the same boundary mismatch.

I haven't run anything; this is from the files and output you pasted. Please confirm the offset against one more admin-screen order, ideally one near midnight. A +5:30 offset fits Asia/Kolkata, but I'm inferring the zone from a single order.

## 2. Fix

`dbt_project.yml`:
```yaml
vars:
  shop_timezone: 'Asia/Kolkata'
```

`models/staging/stg_orders.sql`, in the `renamed` CTE:
```sql
    select
        id as order_id,
        user_id as customer_id,
        coalesce(order_ts, ordered_at) as ordered_at,
        -- raw timestamps are UTC; the shop and ops work in local time
        cast(timezone('{{ var("shop_timezone") }}', timezone('UTC', coalesce(order_ts, ordered_at))) as date) as order_date,
        status

    from source
```

`timezone('UTC', ts)` turns the naive timestamp into a UTC `timestamptz`. The outer call converts it to naive local time. DuckDB needs its ICU extension for this, and it is bundled in the standard wheel.

Update the `order_date` descriptions in `schema.yml` to say "shop-local date (Asia/Kolkata), from `order_ts` (UTC)". Do the same for `first_order` and `most_recent_order`.

**Rebuild `daily_revenue` from scratch.** The incremental window only reaches back 3 days, so it won't repair March and April or the shifted dates:
```
dbt build --full-refresh -s stg_orders+ 
```

Also ask the ingestion owners whether `ordered_at` is deprecated. If it is, drop the `coalesce` and read `order_ts` only.

## 3. Tests that would have caught this

`models/staging/schema.yml`, under `stg_orders`:
```yaml
      - name: order_date
        tests:
          - not_null
```
and the same `not_null` on `orders.order_date` in `models/schema.yml`. That alone would have failed the build on the 43 rows.

A unit test for the timezone logic (also in `models/staging/schema.yml`):
```yaml
unit_tests:
  - name: stg_orders_order_date_is_shop_local
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: null, status: completed, order_ts: '2018-03-15 19:45:20'}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
```

A reconciliation test so a stale incremental table can't pass unnoticed, in `tests/daily_revenue_matches_orders.sql`:
```sql
select o.order_date, o.orders, o.revenue, d.orders as d_orders, d.revenue as d_revenue
from (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
) o
full join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.order_date is distinct from d.order_date
   or o.orders != d.orders
   or abs(o.revenue - d.revenue) > 0.005
```

Optionally, add a `not_null` test on `raw_orders.order_ts` (or an `ordered_at`-or-`order_ts` check) via a source definition. Then a feed change like this one fails at the source, not downstream.
