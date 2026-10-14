claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 36 s | USD 0.084 | 2026-10-14 | project bef6b69 | prompt f253c60e27c9

## 1. Root cause

**`models/staging/stg_orders.sql`** derives the order date with `cast(ordered_at as date)`. `raw_orders.ordered_at` is a naive `TIMESTAMP` holding **UTC**. The shop and its admin screen use **India time (IST, UTC+5:30)**, so any order placed after 18:30 UTC lands on the wrong local day.

The sample data confirms this:

| order | `ordered_at` (UTC) | IST | `orders.order_date` | admin |
|---|---|---|---|---|
| 73 | 2018-03-15 19:45:20 | 03-16 01:15 | 03-15 | 03-16 |
| 16 | 01-17 21:07:25 | 01-18 02:37 | 01-17 | 01-18 |
| 82 | 03-23 23:44:48 | 03-24 05:14 | 03-23 | 03-24 |
| 87, 88 | 03-26 21:12, 21:51 | 03-27 02:42, 03:21 | 03-26 | 03-27 |
| 89 | 03-27 18:45:59 | 03-28 00:15 | 03-27 | 03-28 |
| 12 | 01-15 10:13 | 01-15 15:43 | 01-15 | 01-15 (agrees) |

Orders 82, 87 and 88 account for the March 23 (5 vs 4), 24, 26 (5 vs 3) and 27 differences. Orders placed before 18:30 UTC, such as order 12, are unaffected, which is why only some days disagree. Totals over a month barely move because orders just shift to a neighbouring day. The `orders.order_date` and `customers.first_order` descriptions say "Date (UTC)", so this was documented but never matched what ops needs.

**Why `daily_revenue` agrees with admin but not with `orders`:** it is incremental (`delete+insert`) and only rebuilds the last 3 days. Its older rows (Jan–Mar) match the IST dates, so they were built under an earlier IST-based date logic. Only the recent window is recomputed from the current UTC `order_date`, so history and `orders` have drifted apart. That explains the five dashboard-only dates and the paired off-by-one rows in your diff. I can't see the git history, so this is inferred from the data, but the 5:30 offset on order 73 is exact.

Every test passes because none of them checks that a date means what ops thinks it means, or that the incremental table equals what `orders` would produce.

## 2. Fix

`stg_orders.sql` (India has no DST, so a fixed offset is safe and needs no ICU extension):

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- `ordered_at` is stored in UTC; the shop reports by India local day (UTC+5:30, no DST)
        cast(ordered_at + interval 330 minute as date) as order_date,
        status

    from source
```

Then rebuild the stale history. The incremental logic will never repair it on its own:

```
dbt build --full-refresh -s stg_orders+
```

Update the "Date (UTC)" descriptions in `models/schema.yml` (`orders.order_date`, `customers.first_order`, `customers.most_recent_order`) to say IST. Any change to upstream date logic requires a `--full-refresh` of `daily_revenue`.

## 3. Tests

Pin the timezone semantics with a known case (`tests/assert_order_date_is_ist.sql`):

```sql
-- order 73 was placed 2018-03-15 19:45 UTC = 2018-03-16 01:15 IST
select order_id, order_date
from {{ ref('orders') }}
where order_id = 73
  and order_date != date '2018-03-16'
```

Catch incremental drift (`tests/assert_daily_revenue_matches_orders.sql`):

```sql
with expected as (
    select order_date, count(*) as orders, round(sum(amount), 2) as revenue
    from {{ ref('orders') }}
    group by 1
),

actual as (
    select order_date, orders, round(revenue, 2) as revenue
    from {{ ref('daily_revenue') }}
)

select *
from expected
full outer join actual using (order_date)
where expected.orders is distinct from actual.orders
   or expected.revenue is distinct from actual.revenue
```

Before the fix, the second test would have failed with the 23 rows from your diff. After the fix and the full refresh, both return zero rows.

I haven't run any of this, since I only have the pasted files. Separately, `raw_customer_regions` has an `updated_at` column but is described as "one row per customer", and `region_revenue` joins it with no uniqueness test on `customer_id`. If it holds history, that model double-counts revenue. It's unrelated to the order counts, but worth a `unique` test on `customer_id`.
