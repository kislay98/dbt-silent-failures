claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 58 s | USD 0.008 | 2026-10-14 | project f769fdf+wk6ext | prompt 4d613ca19f07

**1. Root cause**

The ingestion job changed where order timestamps land. From order 57 (2018-03-01) onward, `raw_orders.ordered_at` is NULL and the timestamp is in `raw_orders.order_ts`. Before that, both columns hold the same value. `models/staging/stg_orders.sql` still reads only `ordered_at`:

```sql
ordered_at,
cast(ordered_at as date) as order_date,
```

So the 43 orders from March onward get `order_date = NULL`. The effects:

- `orders` keeps them, but the monthly report groups them under NULL. That NULL bucket is 761.00, which equals March + April revenue in `daily_revenue`.
- `customers.first_order` and `most_recent_order` are NULL for customers such as 9, 12 and 93, even though they have orders.
- `daily_revenue` still shows March and April because it is incremental. Those rows were written before the feed changed. Its rebuild window (`order_date >= max(order_date) - 3 days`) never matches NULL-date rows, so nothing overwrote them.

There is also a time-zone problem. Order 73 is `order_ts = 2018-03-15 19:45:20` and the admin screen shows `2018-03-16 01:15:20`, a difference of +05:30. So `order_ts` is UTC, while ops reconciles in shop local time. Taking `order_ts` as-is would still put order 73 on the wrong day. `models/schema.yml` also documents `order_date` as "Date (UTC)", which conflicts with what ops expects.

I can't tell from the sample which zone `ordered_at` used before March. Rows 54–56 have identical values in both columns. The fix below only converts rows where `ordered_at` is NULL, so January and February keep their current dates, which matches what you said about them. Check a couple of pre-March orders near midnight against the admin screen before deploying.

**2. Fix**

`models/staging/stg_orders.sql`: use `ordered_at` where it exists, and otherwise convert `order_ts` from UTC to shop local time. I've assumed `Asia/Kolkata` from the +05:30 offset. Confirm the shop's zone, since `Asia/Colombo` has the same offset.

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_orders') }}

),

localised as (

    -- the feed moved from ordered_at to order_ts on 2018-03-01 and ordered_at is NULL after that.
    -- order_ts is UTC; ops reconciles against the admin screen in shop local time (UTC+05:30)
    select
        *,
        coalesce(
            ordered_at,
            (order_ts at time zone 'UTC') at time zone 'Asia/Kolkata'
        ) as ordered_at_local

    from source

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at_local as ordered_at,
        cast(ordered_at_local as date) as order_date,
        status

    from localised

)

select * from renamed
```

Update the description in `models/schema.yml` for `orders.order_date` to "Date (shop local time, UTC+05:30) that the order was placed".

Then rebuild. Because `daily_revenue` is incremental and only rewrites the last three days, a full refresh is required to correct the stored March and April rows:

```
dbt build -s stg_orders+ --full-refresh
```

Ask the ingestion owners to confirm the rename and the UTC semantics. The fix should be backed by a source contract, not just this model.

**3. Test that would have caught it**

Add `not_null` tests on `order_date`. Neither `stg_orders` nor `orders` has one today. With the current code, this test fails on 43 rows, and a failing test stops the build.

`models/staging/schema.yml`:

```yaml
  - name: stg_orders
    columns:
      - name: order_date
        tests:
          - not_null
```

`models/schema.yml`, under `orders`:

```yaml
      - name: order_date
        tests:
          - not_null
```

Add two singular tests in `tests/`:

`tests/orders_local_date.sql` pins the time-zone rule to a known order:

```sql
-- order 73: 2018-03-15 19:45:20 UTC is 2018-03-16 01:15:20 shop local, as on the admin screen
select order_id, order_date
from {{ ref('orders') }}
where order_id = 73
  and order_date <> date '2018-03-16'
```

`tests/daily_revenue_reconciles.sql` checks the total that ops reconciles against. It would have failed here, because the dated orders sum to 911 while `daily_revenue` sums to 1672:

```sql
-- every dated order must be in daily_revenue
select 1
from (
    select sum(amount) as orders_total
    from {{ ref('orders') }}
    where order_date is not null
) o
cross join (
    select sum(revenue) as daily_total
    from {{ ref('daily_revenue') }}
) d
where abs(o.orders_total - d.daily_total) > 0.005
```
