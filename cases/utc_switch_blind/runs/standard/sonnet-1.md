claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 25 s | USD 0.099 | 2026-10-15 | project ad0b16b | prompt 9fc7feb7aef0

## 1. Root cause

**`models/staging/stg_orders.sql`** derives the date with `cast(ordered_at as date)`. `raw_orders.ordered_at` is a naive `TIMESTAMP` that is in UTC from mid-March on, and the cast takes the UTC calendar day. Ops compares against the admin screen, which uses India local time (IST, UTC+5:30). Any order placed from 18:30 to 24:00 UTC is already the next day in India, so it lands on the wrong `order_date`.

The sample rows match this:

| Order | `ordered_at` (UTC) | Model `order_date` | Admin day |
|---|---|---|---|
| 73 | 2018-03-15 19:45 | 03-15 | 03-16 (admin has no 03-15) |
| 88 | 2018-03-26 21:51 | 03-26 | 03-27 |
| 92 | 2018-04-01 20:55 | 04-01 | 04-02 (admin has no 04-01) |

This also explains why nothing else broke:
- Monthly totals and `sum(amount)` still match, because orders only move by one day, and mostly within the same month.
- Every test passes, because none of them check which day an order falls on. `schema.yml` even documents `order_date` as "Date (UTC)".

I can't see the seed data, so one point is unconfirmed. Early rows such as order 28 (2018-01-29 22:50) have late-evening times but reportedly agreed with the admin screen. That suggests the feed switched from local time to UTC around mid-March. Check this before fixing (see below).

`daily_revenue` inherits the error from `orders`. It is incremental, so it has a second problem: it only rebuilds the last 3 days.

## 2. Fix

First find the switch-over point. Compare the earliest dates where `raw_orders` evening times disagree with the admin counts, or ask whoever owns the feed. Then, in `stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is naive UTC from {{ var('utc_feed_start', '2018-03-15') }} on; the shop reports in IST (UTC+5:30, no DST)
        cast(
            case
                when ordered_at >= timestamp '{{ var("utc_feed_start", "2018-03-15") }}'
                    then ordered_at + interval 330 minute
                else ordered_at
            end
        as date) as order_date,
        status

    from source

)
```

- If the whole feed turns out to be UTC, drop the `case` and use `cast(ordered_at + interval 330 minute as date)`.
- Update the `order_date` descriptions in `schema.yml` (`orders`, `customers.first_order` and `most_recent_order`) from "UTC" to "IST".
- Rebuild with `dbt build --full-refresh`. The incremental `daily_revenue` would otherwise keep its old rows for dates older than 3 days, and the 3-day window would leave stale buckets.
- `customers.first_order` and `most_recent_order` also come from `order_date`, so they shift too.

## 3. Test

Load the admin screen's daily counts as a seed, `seeds/admin_daily_orders.csv` with columns `order_date,orders`. Then add a singular test, `tests/assert_daily_orders_match_admin.sql`:

```sql
select
    coalesce(a.order_date, d.order_date) as order_date,
    a.orders as admin_orders,
    d.orders as model_orders
from {{ ref('admin_daily_orders') }} a
full outer join {{ ref('daily_revenue') }} d
    on a.order_date = d.order_date
where a.order_date between '2018-03-01' and '2018-04-09'
   or d.order_date between '2018-03-01' and '2018-04-09'
   and coalesce(a.orders, 0) != coalesce(d.orders, 0)
```

Wrap the final `where` in parentheses before committing: `where (a.order_date between ... or d.order_date between ...) and coalesce(a.orders, 0) != coalesce(d.orders, 0)`. Otherwise `and` binds to the second condition only.

This reconciliation test fails on the current output and passes after the fix. To catch the issue without an external reference, also add a guard that flags any timezone-sensitive row. For example, a `dbt_utils.expression_is_true` test on `stg_orders` checking `order_date = cast(ordered_at + interval 330 minute as date)` would fail whenever someone reverts to a plain UTC cast.
