claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 39 s | USD 0.006 | 2026-10-14 | project f769fdf+wk6ext | prompt 4d613ca19f07

## 1. Root cause

**`jaffle_shop/models/staging/stg_orders.sql`** derives `order_date` from `ordered_at`:

```sql
ordered_at,
cast(ordered_at as date) as order_date,
```

In this load, `raw_orders.ordered_at` is NULL for the March and April orders (57 onward in the sample). The populated timestamp is in `order_ts`, which the model never reads. So `order_date` is NULL for those rows, and the 43 orders in the output are the ones with NULL `ordered_at`. The raw columns show this: `order_ts` is populated for every row, and `ordered_at` is NULL from order 57 onward.

Three things follow from that:

- **`orders`** keeps the 43 rows (its join is a left join), so `sum(amount)` stays 1672.00 but the rows fall into a NULL month and March and April disappear from the monthly view.
- **`daily_revenue`** looks correct only because it is stale. It is incremental with `delete+insert`, and it rebuilds only `order_date >= max(order_date) - 3 days`. NULL dates never match that filter, and the March and April rows were written by earlier runs when `ordered_at` was populated. Nothing rebuilds them.
- **`customers`** has NULL `first_order` for customers 9, 12 and 93, which have orders but whose only orders have NULL dates.

There is also a timezone problem. Order 73 has `order_ts = 2018-03-15 19:45:20` and the admin screen shows `2018-03-16 01:15:20`, a +05:30 offset. Even if `order_ts` were used as-is, `cast(... as date)` would give UTC dates, and about 18.5 hours of each day would land on the wrong date compared with the shop's local date. The docs in `models/schema.yml` say `order_date` is "Date (UTC)", but ops reconciles against local time, so the docs and the reconciliation disagree.

**Why the tests passed:** no test checks `order_date`. `models/schema.yml` and `staging/schema.yml` have `not_null` on `order_id`, `customer_id`, `amount`, and so on, but nothing on `order_date`. `daily_revenue`'s `not_null` and `unique` tests only see the rows that are in the table, and the NULL-date orders never reach it.

I inferred the offset from one row (order 73). I can't see the ingestion job, so I don't know for certain which column is the UTC one and which is local. The verification query below settles it.

## 2. Fix

First, confirm which column is local time. Run this against the warehouse:

```sql
select count(*) as rows_where_columns_differ
from raw_orders
where ordered_at is not null
  and ordered_at <> order_ts;
```

The sample shows orders 54–56 with `ordered_at = order_ts`, which doesn't fit a +05:30 offset everywhere. If this count is nonzero, check whether `ordered_at` matches the admin screen on those rows. I'm assuming the zone is `Asia/Kolkata`, since +05:30 fits that and matches the offset on order 73. Ops should confirm the zone name.

Then change the staging model to derive the date from `order_ts` in local time:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        order_ts as ordered_at,
        -- order_ts is UTC; reporting and reconciliation use shop local time
        cast(order_ts at time zone 'UTC' at time zone 'Asia/Kolkata' as date) as order_date,
        status

    from source

)
```

Update the description in `jaffle_shop/models/schema.yml` for `orders.order_date` to say "Date (shop local time, Asia/Kolkata)". The `customers` descriptions for `first_order` and `most_recent_order` also say UTC and need the same change if they should match.

Then rebuild. `daily_revenue` has to be fully refreshed, because the incremental window only touches the last three days:

```
dbt build --full-refresh -s stg_orders+
```

Expected result after the rebuild: `orders` has no NULL `order_date`, March and April show up in the monthly report, order 73 has `order_date = 2018-03-16`, and `daily_revenue` matches `orders` for every day.

## 3. A test that would have caught it

Add a `not_null` test on `order_date` in both places, so it fails at the staging layer and at the output:

**`jaffle_shop/models/staging/schema.yml`** under `stg_orders`:

```yaml
      - name: order_date
        tests:
          - not_null
```

**`jaffle_shop/models/schema.yml`** under `orders`:

```yaml
      - name: order_date
        description: Date (shop local time) that the order was placed, from order_ts
        tests:
          - not_null
```

Also add a row-count check so orders can't silently drop out of the output. A singular test works without any packages:

**`jaffle_shop/tests/orders_cover_raw_orders.sql`**:

```sql
-- every raw order must appear in orders
select r.id
from {{ ref('raw_orders') }} r
left join {{ ref('orders') }} o
    on r.id = o.order_id
where o.order_id is null
```

Finally, pin the timezone with a known case. This test fails if someone changes the conversion and breaks reconciliation with the admin screen:

**`jaffle_shop/tests/order_73_local_date.sql`**:

```sql
-- admin screen shows 2018-03-16 01:15:20 local time for order 73
select order_id
from {{ ref('stg_orders') }}
where order_id = 73
  and order_date <> cast('2018-03-16' as date)
```

The `not_null` test is the one that would have failed on this load: 43 rows fail it on the first run.
