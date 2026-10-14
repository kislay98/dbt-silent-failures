claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 22 s | USD 0.090 | 2026-10-15 | project ad0b16b | prompt ef736a9770e3

## 1. Root cause

**`models/staging/stg_orders.sql`** computes `cast(ordered_at as date) as order_date`. `raw_orders.ordered_at` is a naive `TIMESTAMP` in UTC, so the cast gives the UTC calendar day, not the India day. India is UTC+5:30, so any order placed from 18:30 to 23:59 UTC belongs to the next local day and lands one day early in `orders`.

Order 73 shows this: raw `ordered_at` is `2018-03-15 19:45:20` UTC, which is `2018-03-16 01:15:20` IST. The admin screen shows 16 March, but `orders` has 15 March.

The error only moves orders across midnight, so monthly totals still agree. A few orders at a month boundary could also shift months. Every test passes because nothing checks the day boundary. The `orders.order_date` description even says "Date (UTC)", so the model matches its docs but not what the business needs.

**Why `daily_revenue` disagrees with `orders`:** `models/daily_revenue.sql` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. Its January to March rows agree with the admin screen, so they must have been written when `order_date` was still the local day. I haven't seen that earlier version of the code, so this is inferred from the symptoms. When `stg_orders` switched to the UTC cast, only the last three days of April were recomputed. The older rows stayed as they were and now disagree with `orders`. `customers.first_order` and `most_recent_order` have the same UTC problem, since they use `order_date`.

## 2. Fix

Change `stg_orders.sql`:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is UTC; the business day is India local time
        cast((ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata' as date) as order_date,
        status
```

India has no DST, so `cast(ordered_at + interval 330 minute as date)` is an equivalent option if ICU time zones are unavailable.

Then rebuild everything downstream, including the incremental model, whose history won't self-heal:

```
dbt build --full-refresh
```

Also update the `order_date`, `first_order` and `most_recent_order` descriptions in `schema.yml` from "Date (UTC)" to "Date (IST)".

I haven't run any of this against your warehouse. After the rebuild, check the 5 to 18 January and 20 to 31 March counts against the admin screen. Order 73 should show 2018-03-16.

## 3. Tests that would have caught it

A singular test, `tests/assert_order_date_is_ist.sql`, which returns rows on failure:

```sql
select o.order_id, o.order_date, cast(r.ordered_at + interval 330 minute as date) as expected
from {{ ref('orders') }} o
join {{ ref('raw_orders') }} r on r.id = o.order_id
where o.order_date <> cast(r.ordered_at + interval 330 minute as date)
```

It is written independently of the model's cast, so it catches a UTC regression. A pinned case such as order 73 on 2018-03-16 would also work.

A second singular test, `tests/assert_daily_revenue_matches_orders.sql`, catches a stale incremental history:

```sql
select coalesce(d.order_date, o.order_date) as order_date, d.orders as dr_orders, o.orders as o_orders
from {{ ref('daily_revenue') }} d
full outer join (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
) o on d.order_date = o.order_date
where d.orders is distinct from o.orders
   or abs(coalesce(d.revenue, 0) - coalesce(o.revenue, 0)) > 0.005
```
