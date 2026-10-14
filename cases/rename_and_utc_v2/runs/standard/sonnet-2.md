claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 41 s | USD 0.048 | 2026-10-15 | project ad0b16b | prompt 15c0aae6d239

## 1. Root cause

**File:** `models/staging/stg_orders.sql` reads only `ordered_at`: `cast(ordered_at as date) as order_date`.

**Data:** the latest load of `raw_orders` has `ordered_at` NULL for all 43 March and April orders. The timestamp now arrives only in a new column, `order_ts`. In Jan and Feb the two columns are identical, so the rename went unnoticed until the March rows came in. The 43 NULL-date orders match the 35 + 8 March/April orders in `daily_revenue`. Order 73 is one of them.

**Why `order_ts` can't be used as-is.** For order 73, `order_ts` is 19:45:20 on 03-15 and the admin shows 01:15:20 on 03-16, a 5h30m difference. So `order_ts` is not shop-local time. Where both columns exist (Jan/Feb) they are equal, so the old `ordered_at` was shop-local. Cutting over to `order_ts` unconverted would put late-evening orders on the wrong day. That would break the daily reconciliation even without the NULLs.

**Why everything stayed green:**
- No `not_null` test exists on `stg_orders.ordered_at`, `stg_orders.order_date` or `orders.order_date`. The `unique` and `not_null` tests cover only `order_id`, `customer_id`, `amount` and the amount columns.
- The NULL-date orders still join to their payments, so `sum(amount)` stays at 1672.00.
- `daily_revenue` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. That filter drops NULL dates, so it kept its old March/April rows and ignored the broken ones. The `unique` and `not_null` tests on it passed, and the problem was hidden there.
- `customers` is also affected: only 42 of 100 customers have `first_order` and `most_recent_order`.

## 2. Fix

I couldn't confirm the shop's time zone from the data. One row gives a +5:30 offset, which fits `Asia/Kolkata`, but the region and AUD data suggest an Australian shop. The offset may also vary with daylight saving. Confirm with the ingestion owner what zone `order_ts` is in, and check a few March and April orders (especially around 1 April) against the admin screen. Use a named time zone rather than a fixed interval so daylight saving is handled.

`dbt_project.yml`:
```yaml
vars:
  order_ts_timezone: 'UTC'
  shop_timezone: 'Asia/Kolkata'   # confirm; must reproduce the admin screen
```

`stg_orders.sql`:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ordered_at is shop-local; order_ts replaced it from the March load and is not local
        coalesce(
            ordered_at,
            timezone('{{ var("shop_timezone") }}', timezone('{{ var("order_ts_timezone") }}', order_ts))
        ) as ordered_at,
        status

    from source

),

final as (

    select
        order_id,
        customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status

    from renamed

)

select * from final
```

Before rebuilding, compare the new dates against the existing `daily_revenue` rows. They came from the earlier correct load, so they should match day for day. Then rebuild with `dbt build --full-refresh -s stg_orders+`. The full refresh matters because `daily_revenue` only rebuilds the last three days.

Also ask the ingestion owner to either keep populating `ordered_at` or document the `order_ts` change.

## 3. Tests

In `models/staging/schema.yml` and `models/schema.yml`:
```yaml
      - name: ordered_at      # stg_orders
        tests: [not_null]
      - name: order_date      # stg_orders, and orders in models/schema.yml
        tests: [not_null]
```

A singular test, `tests/daily_revenue_matches_orders.sql`, catches stale or missing days in the incremental model:
```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
)
select coalesce(o.order_date, d.order_date) as order_date
from o
full join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.order_date is null or d.order_date is null
   or o.orders != d.orders or abs(o.revenue - d.revenue) > 0.005
```

The first tests alone would have failed the nightly build with 43 NULL `order_date` values. The singular test would have flagged the divergence between `orders` and `daily_revenue`.
