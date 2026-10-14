claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 31 s | USD 0.101 | 2026-10-15 | project ad0b16b | prompt 11fb6c29d933

## 1. Root cause

**`models/staging/stg_orders.sql`** reads only `raw_orders.ordered_at`:

```sql
cast(ordered_at as date) as order_date
```

Starting with the March load, the ingestion job stopped filling `ordered_at` and writes the timestamp to a new column, `raw_orders.order_ts`. In the samples, `ordered_at` is NULL for every March/April order and `order_ts` is populated. Exactly 43 of the 99 orders are affected, which is every order from March 1 onward. They get a NULL `order_date` in `stg_orders`, then in `orders`, and so they fall out of the monthly report (the NULL month at the bottom is the 43 orders / 761.00). `sum(amount)` still ties at 1672.00 because `amount` doesn't depend on the date.

Why nothing failed:
- `order_date` has no `not_null` test in either `schema.yml`.
- `orders` uses a left join, so the rows survive.
- `daily_revenue` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. NULL-dated rows never match that filter, so the March and April rows from earlier runs stay frozen and look fine.

Don't just swap in `order_ts`. The Jan–Feb values in `ordered_at` are in shop-local time, since they matched the admin screen daily. The new `order_ts` is very likely UTC, so the date can shift. For example, order 73 shows 2018-03-16 01:15:20 locally, but if `order_ts` is UTC it falls on 03-15 (the shop looks Australian, UTC+11 in March under DST, UTC+10 after April 1). I couldn't see order 73's `order_ts` in the sample. Check it first: it should be about 2018-03-15 14:15:20 if the zone is Sydney/Melbourne. Also confirm the shop's zone. QLD and WA have no DST, so the zone name matters.

## 2. Fix

In `stg_orders.sql`, use `ordered_at` where present and otherwise convert `order_ts` from UTC to the shop's zone:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        coalesce(
            ordered_at,
            timezone('{{ var("shop_timezone", "Australia/Sydney") }}', timezone('UTC', order_ts))
        ) as ordered_at,
        cast(
            coalesce(
                ordered_at,
                timezone('{{ var("shop_timezone", "Australia/Sydney") }}', timezone('UTC', order_ts))
            ) as date
        ) as order_date,
        status

    from source

)
```

Then rebuild, because the incremental model won't repair itself:

```
dbt build --full-refresh -s stg_orders+
```

After the rebuild, order 73 should land on 2018-03-16, there should be no NULL `order_date`, and `orders` should show Jan–Apr. Also update the `order_date` descriptions in `schema.yml`: they say "UTC", but the values are shop-local.

Longer term, ask the ingestion owner to confirm the `order_ts` semantics (UTC or local) and whether `ordered_at` is being retired. Once it is, drop the coalesce and read `order_ts` only.

## 3. Tests

In `models/staging/schema.yml` and `models/schema.yml`, add `not_null` to `order_date`:

```yaml
      - name: order_date
        tests:
          - not_null
```

That alone would have failed this build with 43 rows. Add two singular tests as well.

`tests/assert_daily_revenue_matches_orders.sql` catches stale or frozen incremental output:

```sql
select o.order_date, o.orders, d.orders as daily_orders
from (select order_date, count(*) as orders from {{ ref('orders') }} group by 1) o
full join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
```

`tests/assert_order_73_local_date.sql` pins the timezone behaviour against the admin screen:

```sql
select * from {{ ref('orders') }}
where order_id = 73 and order_date is distinct from date '2018-03-16'
```

Optionally, add a `not_null` test on `ordered_at` and `order_ts` using `coalesce`, so the next change in the feed's shape also fails loudly.
