claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 53 s | USD 0.236 | 2026-10-15 | project ad0b16b | prompt 63b47184243a

I couldn't find the project or seed CSVs on this machine (the working directory is empty and there's no `.duckdb` file), so this analysis is based only on what you pasted. I haven't run anything below.

## 1. Root cause: the timestamps in `seeds/raw_orders.csv` shifted in mid-March

This is a data problem in `seeds/raw_orders.csv`, not a model bug.

- `stg_orders.sql` does `cast(ordered_at as date) as order_date`. `ordered_at` is a plain `TIMESTAMP` with no time zone, so `order_date` is simply the calendar date of whatever clock the feed wrote.
- Up to mid-March, the feed wrote the shop's local time. That's why the daily counts always matched the admin screen.
- From about order 73 (15/16 March) onward, the feed writes a clock that is **5h30m behind** local time. Order 73 is 2018-03-16 01:15:20 in the app but 2018-03-15 19:45:20 in `raw_orders`.
- Orders placed before about 05:30 local now land on the previous day. Your numbers fit this exactly:

| Date | App | Shifted | Here |
|---|---|---|---|
| 26 Mar | 3 | +2 from the 27th | 5 |
| 27 Mar | 2 | −2, +1 from the 28th | 1 |
| 28 Mar | 1 | −1 | 0 |

  Orders only move by hours, so monthly totals stay the same, and amounts are untouched (1672.00 in both).
- No test failed because every test checks nulls, uniqueness, accepted values or relationships. Nothing checks the timestamps themselves.

An offset of 5:30 isn't an Australian zone, even though the data uses AUD and Australian states. That suggests the export changed servers or settings, rather than "it's UTC now". Confirm the cause with whoever owns the orders export.

## 2. Fix

**Preferred: fix the data.** Ask for `raw_orders` to be re-exported from the cutover onward in the original local-time convention, and replace the affected rows in `seeds/raw_orders.csv`.

**Interim: correct it in staging.** First, find the first affected order. A backward jump of 5.5h will often show up as `ordered_at` going backwards while `id` increases:

```sql
select id, ordered_at, lag(ordered_at) over (order by id) as prev_ordered_at
from raw_orders
qualify ordered_at < prev_ordered_at
order by id;
```

Then check a few orders around that id against the admin screen. Once the cutover id is confirmed, change `models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- from order {{ var('orders_offset_from_id') }} the export writes timestamps 5h30m behind shop local time
        case
            when id >= {{ var('orders_offset_from_id') }} then ordered_at + interval 330 minute
            else ordered_at
        end as ordered_at,
        status

    from source

),

final as (

    select *, cast(ordered_at as date) as order_date
    from renamed

)

select * from final
```

Add `vars: {orders_offset_from_id: <confirmed id>}` to `dbt_project.yml`.

**Rebuild with a full refresh:** `dbt build --full-refresh`. `daily_revenue` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`, so a normal run would leave the wrong March rows in place.

`customers.first_order` and `most_recent_order` will also change for the affected orders.

Separately, `schema.yml` documents `order_date`, `first_order` and `most_recent_order` as "Date (UTC)". In reality they are shop-local dates. Correct those descriptions so the next person isn't misled.

## 3. Tests that would have caught this

**Reconcile against the app.** This is the robust option, and it's the same check ops does by hand. Load the admin screen's daily counts as a source or seed (`app_daily_orders`), then add `tests/assert_daily_orders_match_app.sql`:

```sql
select coalesce(d.order_date, a.order_date) as order_date, d.orders, a.orders as app_orders
from {{ ref('daily_revenue') }} d
full outer join {{ ref('app_daily_orders') }} a on d.order_date = a.order_date
where coalesce(d.orders, 0) != coalesce(a.orders, 0)
```

This would have failed on 26–28 March.

**Cheap guard with no extra data:** `tests/assert_ordered_at_monotonic.sql`.

```sql
select * from (
    select order_id, ordered_at, lag(ordered_at) over (order by order_id) as prev_ordered_at
    from {{ ref('stg_orders') }}
) where ordered_at < prev_ordered_at
```

This assumes order ids are issued in time order, as they are in jaffle_shop. It fires on a backward clock shift only if two consecutive orders were less than 5.5h apart at the cutover. I can't confirm that without the seed data, so treat this as a tripwire, not a guarantee.

**Pin the known case with a unit test.** Add a dbt unit test on `stg_orders` using order 73: input `2018-03-15 19:45:20`, expect `order_date = 2018-03-16`. This locks in the fix.
