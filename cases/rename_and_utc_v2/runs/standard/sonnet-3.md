claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 34 s | USD 0.102 | 2026-10-15 | project ad0b16b | prompt 15c0aae6d239

## 1. Root cause

**Files:** the ingestion job's change to `raw_orders`, and `models/staging/stg_orders.sql`, which only reads the old column.

- Starting with this load, the ingestion job populates `raw_orders.order_ts` instead of `ordered_at` for new orders. `ordered_at` is `NULL` for all 43 March and April orders, and `order_ts` is filled in. Jan and Feb rows have both columns, and they are identical in the sample.
- `stg_orders` does `cast(ordered_at as date) as order_date` and never looks at `order_ts`. So `order_date` is `NULL` for those 43 orders. They fall into the `NULL` month bucket in `orders`, which holds 43 orders and 761.00, equal to March (622) plus April (139) in `daily_revenue`.
- The two columns are not in the same timezone. For order 73, `order_ts` 2018-03-15 19:45:20 versus the admin screen's 2018-03-16 01:15:20 is a +5:30 offset. So `order_ts` is UTC, and the old `ordered_at` was the shop's local time. That is why ops reconciled by day until now. A plain `coalesce(ordered_at, order_ts)` would put late-evening UTC orders on the wrong day.
- Why nothing failed:
  - There is no `not_null` test on `order_date` in `orders` or `stg_orders`.
  - `daily_revenue` is incremental and only rebuilds the last 3 days from its own `max(order_date)`. Its filter `order_date >= ...` drops `NULL`s, so it kept the March and April rows from earlier runs.
  - `sum(amount)` is unchanged because the payments joins don't depend on the date.

## 2. Fix

In `dbt_project.yml`, add a variable. +5:30 is the only evidence I have, from one order, so please confirm the shop's timezone with ops and check a few more admin rows:

```yaml
vars:
  shop_timezone: 'Asia/Kolkata'
```

In `stg_orders.sql`, replace the date columns:

```sql
    select
        id as order_id,
        user_id as customer_id,
        -- ordered_at is shop-local; the ingestion job now sends order_ts, which is UTC
        coalesce(
            ordered_at,
            (order_ts at time zone 'UTC') at time zone '{{ var("shop_timezone") }}'
        ) as ordered_at,
        cast(coalesce(
            ordered_at,
            (order_ts at time zone 'UTC') at time zone '{{ var("shop_timezone") }}'
        ) as date) as order_date,
        status
    from source
```

For order 73, 19:45:20 UTC becomes 01:15:20 local, so `order_date` is 2018-03-16, matching the admin screen. Using `coalesce` keeps Jan and Feb as they were.

Because `daily_revenue` only rebuilds the last 3 days and its old rows are stale, run:

```
dbt build --full-refresh -s stg_orders+
```

Also fix the docs in `schema.yml`: `order_date`, `first_order` and `most_recent_order` say "Date (UTC)" and "from the ordered_at timestamp". They should say shop-local date.

Ask the ingestion owners to confirm the `ordered_at` to `order_ts` switch and the UTC semantics, so this doesn't change again silently.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, under `stg_orders`:

```yaml
      - name: order_date
        tests:
          - not_null
```

Add the same `not_null` to `orders.order_date`. This test alone would have failed with 43 rows.

A singular test, `tests/orders_match_admin_dates.sql`, pins the known reconciliation point and catches timezone regressions:

```sql
select * from {{ ref('orders') }}
where order_id = 73 and order_date <> date '2018-03-16'
```

A singular test, `tests/daily_revenue_reconciles_to_orders.sql`, catches stale incremental data:

```sql
select 1
where (select round(sum(revenue), 2) from {{ ref('daily_revenue') }})
   <> (select round(sum(amount), 2) from {{ ref('orders') }} where order_date is not null)
   or (select sum(orders) from {{ ref('daily_revenue') }})
   <> (select count(*) from {{ ref('orders') }})
```

I haven't run any of this, since the project files aren't in my working directory. The 5:30 offset comes from one order, so confirm it before relying on it.
