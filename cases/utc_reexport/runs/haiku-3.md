claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 56 s | USD 0.008 | 2026-10-14 | project bef6b69 | prompt f253c60e27c9

## 1. Root cause

**`jaffle_shop/models/staging/stg_orders.sql`** derives `order_date` with `cast(ordered_at as date)`. `ordered_at` is a naive `TIMESTAMP` in UTC, so this takes the UTC calendar day. The shop and the admin screen use India time (UTC+05:30), so any order placed after 18:30 UTC falls on the next day locally.

The sample data confirms this:

| order_id | ordered_at (UTC) | warehouse `order_date` | India local time | Admin day |
|---|---|---|---|---|
| 73 | 2018-03-15 19:45:20 | 15 Mar | 2018-03-16 01:15 | 16 Mar |
| 16 | 2018-01-17 21:07:25 | 17 Jan | 2018-01-18 02:37 | 18 Jan |
| 87 | 2018-03-26 21:12:29 | 26 Mar | 2018-03-27 02:42 | 27 Mar |
| 88 | 2018-03-26 21:51:20 | 26 Mar | 2018-03-27 03:21 | 27 Mar |
| 89 | 2018-03-27 18:45:59 | 27 Mar | 2018-03-28 00:15 | 28 Mar |

Moving order 16 from the 17th to the 18th gives 17 Jan = 3 and 18 Jan = 2, which matches the admin counts. Orders 87 and 88 moving into the 27th give 2, which matches the admin count. The mismatches are all orders placed between 18:30 and 24:00 UTC.

`schema.yml` says `orders.order_date` is "Date (UTC)", so the UTC choice was documented, but it is not what the business uses. The definition should be India local day.

**`jaffle_shop/models/daily_revenue.sql`** is the second problem. It is incremental and only rebuilds the last three days, so the Jan–Mar rows are never recomputed. They still hold values from when the logic was IST-based, which is why they match the admin screen. Only the recent window tracks the current UTC `orders`. In the sample, the disagreements stop at 3 April, which is consistent with the rebuilt window starting in early April. Any change to `orders` is therefore never applied to older history. I can't see git history, so I can't confirm when `stg_orders` changed, but this explains the pattern.

**Why the build passed:** the tests only check `unique`, `not_null`, and `accepted_values`. Nothing checks what `order_date` means, and nothing reconciles `daily_revenue` to `orders`.

## 2. Fix

**a) `jaffle_shop/models/staging/stg_orders.sql`:** derive the date in India time.

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is UTC; the shop works in India time (UTC+05:30, no DST)
        cast(ordered_at + interval 330 minute as date) as order_date,
        status

    from source

)
```

If you prefer a named zone, `cast(timezone('Asia/Kolkata', timezone('UTC', ordered_at)) as date)` also works, but it needs the ICU extension in DuckDB.

**b) `jaffle_shop/models/daily_revenue.sql`:** make it a full table. `orders` is already a full rebuild, so the incremental window saves nothing, and it is the reason history went stale.

```sql
{{
    config(
        materialized='table'
    )
}}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

**c) `jaffle_shop/models/schema.yml`:** update the descriptions that say "Date (UTC)" for `orders.order_date`, `customers.first_order`, and `customers.most_recent_order` to say "Date (India local time, UTC+05:30)". The `customers` description for `total_order_amount` also refers to a column that does not exist in the model.

**d) Run:** `dbt build --full-refresh`. The project is small, so a full refresh is cheap and also rebuilds any stale history. I have not run this against the warehouse. I checked the arithmetic by hand against orders 16, 73, 87, 88, and 89 and the admin counts for the 17th, 18th, and 27th.

## 3. Test that would have caught it

Two tests. I've written them as singular tests so they need no extra packages. Put them in `jaffle_shop/tests/`.

**`tests/assert_order_date_is_india_local_day.sql`:** pins the definition with a known order and fails if `order_date` drifts back to the UTC day.

```sql
-- order_date must be the India (UTC+05:30) day of ordered_at.
-- Order 73: 2018-03-15 19:45 UTC is 2018-03-16 01:15 in India.
select order_id, ordered_at, order_date
from {{ ref('stg_orders') }}
where order_date <> cast(ordered_at + interval 330 minute as date)
   or (order_id = 73 and order_date <> date '2018-03-16')
```

**`tests/assert_daily_revenue_matches_orders.sql`:** reconciles the dashboard table to `orders`. This would have failed on the current data, because the stale Jan–Mar rows disagree with `orders`.

```sql
with from_orders as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_from_orders,
    d.orders as orders_from_daily_revenue
from from_orders o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```
