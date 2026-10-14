claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 29 s | USD 0.074 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

The bug is in `models/staging/stg_orders.sql`: `cast(ordered_at as date) as order_date`. It takes the calendar date of a naive `TIMESTAMP`, so the result is only right if `ordered_at` is already in India local time. `raw_orders.ordered_at` is a plain `TIMESTAMP` with no timezone, and I believe the feed changed from IST to UTC around mid-March.

- **Evidence:** The rows shown for 23-28 March are consistent with UTC, not IST:
  - Order 82 at `2018-03-23 23:44:48` is 05:14 IST on the 24th. The admin screen shows 4 orders on the 23rd and 2 on the 24th, but we have 5 and 1.
  - Orders 87 and 88 (21:12 and 21:51) fall on the 27th in IST. The admin screen shows 3 on the 26th and 2 on the 27th, but we have 5 and 1.
  - Order 89 at 18:45:59 UTC is 00:15 IST on the 28th. The admin screen shows 1 on the 28th, but we have no row for that date.
  - The first mismatch is 15 March (we have one order, the admin screen has none; admin shows one on the 16th). Everything before that agrees.
- **Why only some days:** Any order placed between 18:30 and 24:00 UTC lands on the wrong IST day, and only these shift. Orders earlier in the UTC day get the same date either way.
- **Why nothing else caught it:** Monthly totals match because the shifts mostly stay within a month, with a few exceptions at month boundaries (1 April appears in our output). `sum(amount)` is unchanged because no order is dropped. No test looks at the date logic.
- **What I can't see:** The exact cutover point isn't visible without the seed data. It is somewhere between the last agreeing order on 14 March and the first mismatch on 15 March. Confirm it with whoever owns the feed.
- **Related issue:** `schema.yml` documents `order_date` as "Date (UTC)", which is why this went unnoticed. `daily_revenue` is also affected, and because it only rebuilds the last 3 days, its older rows stay wrong until a full refresh.

## 2. Fix

Normalise to one timezone in staging, then derive the local date from it. In `stg_orders.sql`:

```sql
{% set utc_from = var('orders_utc_from', '2018-03-15 00:00:00') %}  -- confirm exact cutover with feed owner

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- before the cutover the feed was India local time, after it UTC; normalise to IST
        case
            when ordered_at < timestamp '{{ utc_from }}' then ordered_at
            else ordered_at + interval 5 hours 30 minutes
        end as ordered_at_ist,
        cast(
            case
                when ordered_at < timestamp '{{ utc_from }}' then ordered_at
                else ordered_at + interval 5 hours 30 minutes
            end as date
        ) as order_date,
        status

    from source

)
```

Then:
- Update the `order_date`, `first_order` and `most_recent_order` descriptions to say "Date (Asia/Kolkata)".
- Run `dbt build --full-refresh` so `daily_revenue` drops the old wrong rows.
- Longer term, load `ordered_at` as a `TIMESTAMPTZ` or ask the source to emit a consistent timezone.

## 3. Test

Reconcile against the admin screen's counts. Load them as a seed, `seeds/admin_daily_orders.csv` with columns `day,orders`, containing the table from ops. Then add a singular test, `tests/assert_daily_orders_match_admin.sql`:

```sql
with ours as (
    select order_date as day, count(*) as orders
    from {{ ref('orders') }}
    where order_date between '2018-03-01' and '2018-04-09'
    group by 1
),
admin as (
    select cast(day as date) as day, orders from {{ ref('admin_daily_orders') }}
)
select coalesce(ours.day, admin.day) as day, ours.orders as ours, admin.orders as admin
from ours
full outer join admin on ours.day = admin.day
where ours.orders is distinct from admin.orders
```

This fails on the current code, with 15 and 16 March, 23 to 28 March and 1 to 3 April among the mismatches, and passes after the fix. If the cutover guess is slightly off, it will also show which days still disagree.

I haven't run any of this, since the seed data and project aren't available here.
