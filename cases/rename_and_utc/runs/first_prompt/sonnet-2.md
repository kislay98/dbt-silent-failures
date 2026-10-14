claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 31 s | USD 0.079 | 2026-10-14 | project f769fdf+wk6ext | prompt c5676332c543

## 1. Root cause

The ingestion job changed the shape of `raw_orders`. Starting at order 57 (2018-03-01) it leaves `ordered_at` NULL and writes the timestamp only to a new column, `order_ts`. For orders 54-56, where both columns are filled, they hold identical values. That makes 43 orders (March and April) have no `ordered_at`.

`models/staging/stg_orders.sql` derives the date only from the old column:

```sql
cast(ordered_at as date) as order_date
```

So `order_date` is NULL for those 43 orders. It flows into `orders` and `customers` (`first_order` and `most_recent_order` are NULL for customers 9, 12 and 93). In the month report the 43 orders land in a NULL group, which is why only January and February show.

Nothing failed because `order_date` has no tests in either `schema.yml` (`stg_orders` has none; `orders.order_date` has only a description), and no schema or freshness check covers the raw tables.

`daily_revenue` still looks right for two reasons:
- It is incremental, so the March and April rows are stale leftovers from earlier runs.
- Its `where order_date >= max(order_date) - 3 days` filter silently drops NULL dates. A `--full-refresh` would drop March and April and put the 43 orders in a NULL group, which would then fail `not_null`. The totals still match (`1672.00`) only because of the stale rows.

**Timezone issue.** Order 73 is `2018-03-15 19:45:20` in `order_ts` but `2018-03-16 01:15:20` on the admin screen. That is a +5:30 offset, so `order_ts` (and the old `ordered_at`) is UTC and the shop is on India time (Asia/Kolkata, which I inferred from the offset and which you should confirm). A plain `cast(order_ts as date)` would put order 73 on 03-15 and mismatch ops' daily reconciliation, since `schema.yml` documents `order_date` as UTC but ops compares against local time.

## 2. Fix

I haven't run any of this.

`stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        coalesce(ordered_at, order_ts) as ordered_at,
        -- timestamps are UTC; order_date is the shop's local date, to match the admin screen
        cast(timezone('Asia/Kolkata', timezone('UTC', coalesce(ordered_at, order_ts))) as date) as order_date,
        status

    from source

)
```

Update the `order_date` descriptions in `models/schema.yml` (and the `customers` order date columns) from "Date (UTC)" to "shop-local date".

Then rebuild `daily_revenue` from scratch, since the incremental run only touches the last three days:

```
dbt build --full-refresh -s stg_orders+
```

Converting to local time also moves any January or February order placed after 18:30 UTC to the next day. That should match the admin screen, but tell ops to expect small shifts in those months. If you'd rather keep `order_date` in UTC, drop the `timezone(...)` wrapper, but then order 73 will not reconcile.

Separately, ask the ingestion owner whether `ordered_at` is deprecated in favour of `order_ts`.

## 3. Tests

In `models/staging/schema.yml`, under `stg_orders`:

```yaml
      - name: order_date
        tests:
          - not_null
```

Add the same `not_null` to `orders.order_date` in `models/schema.yml`.

A reconciliation test, `tests/assert_daily_revenue_matches_orders.sql`. It would also have caught the stale incremental table:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
)
select *
from o
full join {{ ref('daily_revenue') }} d
    on o.order_date is not distinct from d.order_date
where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

A regression test for the timezone, `tests/assert_order_73_local_date.sql`:

```sql
select * from {{ ref('stg_orders') }}
where order_id = 73 and order_date != date '2018-03-16'
```

The `not_null` test on `order_date` would have failed on the first night the column went NULL. The reconciliation test would have failed as soon as `orders` and `daily_revenue` diverged.
