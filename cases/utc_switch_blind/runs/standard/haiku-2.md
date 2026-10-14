claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 54 s | USD 0.009 | 2026-10-15 | project ad0b16b | prompt 9fc7feb7aef0

## 1. Root cause

**The timezone of `raw_orders.ordered_at` changed in mid-March, and `stg_orders.sql` doesn't account for it.**

- `jaffle_shop/models/staging/stg_orders.sql` derives the date with `cast(ordered_at as date) as order_date`. That takes the wall-clock date of a naive `TIMESTAMP` with no zone conversion. `schema.yml` documents `order_date` as "Date (UTC)", but the data does not follow one convention.
- Before the change, `ordered_at` was local India time (Asia/Kolkata), which matches the admin screen. After it, `ordered_at` is UTC. An order placed after 18:30 UTC is 00:00 or later in India, so the warehouse files it under the previous day while the admin screen shows the next day.

The evidence in the sample rows:

- **Order 73**, `ordered_at` 2018-03-15 19:45:20. The warehouse puts it on 03-15. In IST it is 01:15 on 03-16. The admin screen has no 03-15 row and shows one more order on 03-16.
- **Order 92**, `ordered_at` 2018-04-01 20:55:29. The warehouse puts it on 04-01. In IST it is 02:25 on 04-02. The admin screen has no 04-01 row and shows one more order on 04-02.
- **Order 28**, `ordered_at` 2018-01-29 22:50:40. If January were UTC, the admin screen would show 01-30, and ops would have seen disagreements in January. They did not, so January's `ordered_at` is already local time.

This explains the symptoms. Only evening orders move, so only some days disagree. Each order moves by exactly one day, so monthly totals and `sum(amount)` still match.

Caveat: I can only see 10 rows of `raw_orders`, so the exact cutover is inferred to fall between 1 and 15 March. Confirm it with this query:

```sql
select id as order_id, ordered_at,
       cast(ordered_at as date) as warehouse_day,
       cast(ordered_at + interval '5 hours 30 minutes' as date) as ist_day
from raw_orders
where cast(ordered_at as time) >= time '18:30:00'
  and ordered_at >= timestamp '2018-03-01'
order by ordered_at;
```

The cutover is the first row where the admin screen shows `ist_day` rather than `warehouse_day`.

Downstream, `orders.order_date`, `daily_revenue.order_date`, and `customers.first_order`/`most_recent_order` are all affected. `region_revenue` is not.

## 2. Fix

Put every `ordered_at` in one zone, and make the staging model convert to local time explicitly. Replace the `order_date` expression in `stg_orders.sql`, using the cutover found above:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        case
            when ordered_at >= timestamp '<CUTOVER>'  -- first UTC-stamped order
                then cast(ordered_at + interval '5 hours 30 minutes' as date)
            else cast(ordered_at as date)
        end as order_date,
        status

    from source

)
```

The longer-term fix is upstream: have the feed send one zone, or store `timestamptz`. Then convert with `ordered_at at time zone 'Asia/Kolkata'`. Either way, update the `schema.yml` descriptions for `order_date` from "Date (UTC)" to the zone you actually use.

Then rebuild everything with `dbt build --full-refresh`. A plain run is not enough. `daily_revenue` is incremental and only rebuilds the last three days, so March rows would keep their old counts.

## 3. Test that would have caught it

Every existing test checks shape: uniqueness, not-null, accepted values. None checks that `order_date` matches the business's own day count, which is the thing ops reconciles against. Add a singular test that compares `daily_revenue` with the admin export. First, load the admin counts as a seed, `seeds/admin_daily_order_counts.csv` with columns `order_date,orders`, using the table from the question.

```sql
-- tests/assert_daily_orders_match_admin.sql
select
    coalesce(d.order_date, a.order_date) as order_date,
    a.orders as admin_orders,
    d.orders as warehouse_orders
from {{ ref('admin_daily_order_counts') }} a
full outer join {{ ref('daily_revenue') }} d
    on a.order_date = d.order_date
where coalesce(d.order_date, a.order_date) between date '2018-03-01' and date '2018-04-09'
  and a.orders is distinct from d.orders
```

Run against the current data, this test fails on about a dozen days, such as 2018-03-15 and 2018-04-01. Once the fix is in, it passes. The seed will go stale as the data grows, so refresh it from the admin screen when the range changes.
