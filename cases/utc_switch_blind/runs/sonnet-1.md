claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 26 s | USD 0.072 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`models/staging/stg_orders.sql`** derives the date with `cast(ordered_at as date)`. `raw_orders.ordered_at` is a naive `TIMESTAMP` holding UTC, so `order_date` is the UTC calendar date. The admin screen shows India time (IST, UTC+5:30), so any order placed between 18:30 and 23:59 UTC is counted a day earlier in `orders` than on the admin screen.

The sample data matches this:
- Order 82 is `2018-03-23 23:44:48` UTC, which is 05:14 IST on 03-24. The model counts it on 03-23 (5 vs admin 4), and 03-24 comes up short.
- Orders 87 (21:12) and 88 (21:51) are on 03-27 in IST. The model has them on 03-26 (5 vs admin 3).
- Order 89 (18:45:59) is 00:15 IST on 03-28. The model has it on 03-27, and admin shows 03-28 = 1.

Nothing else is wrong. Monthly totals and `sum(amount)` still match because orders only shift by a day. No test failed because every test checks structure, never the date semantics. The `schema.yml` description even documents "Date (UTC)".

One thing I couldn't verify, because the seed isn't included: why Jan–mid-March agreed. Either no order fell in the 18:30–24:00 UTC window before then, or the feed's timestamp convention changed around 03-15. Check with:

```sql
select count(*) filter (where ordered_at::time >= '18:30') as late_utc, min(ordered_at), max(ordered_at)
from raw_orders where ordered_at < '2018-03-15';
```

If that returns 0 late rows, the explanation is coincidence and the fix below is right for all rows. If early rows do sit in that window and previously matched admin, those timestamps were already local, and the conversion should apply only from the cutover onward.

## 2. Fix

In `stg_orders.sql`, convert to IST before taking the date. India has no DST, so a fixed offset is safe:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at + interval 330 minute as date) as order_date,
        status
    from source
```

Then update the `order_date` and `first_order`/`most_recent_order` descriptions to say "Date (IST)". `daily_revenue` is incremental and only rebuilds the last three days, so history stays wrong until you run:

```
dbt build --full-refresh -s stg_orders+
```

## 3. Test

Add the admin counts as a seed (`seeds/admin_daily_orders.csv` with `day,orders`) and a singular test, `tests/assert_orders_match_admin_daily_counts.sql`:

```sql
with mine as (
    select order_date as day, count(*) as orders
    from {{ ref('orders') }}
    where order_date >= '2018-03-01'
    group by 1
)
select coalesce(m.day, a.day) as day, m.orders as model_orders, a.orders as admin_orders
from mine m
full outer join {{ ref('admin_daily_orders') }} a on m.day = a.day
where m.orders is distinct from a.orders
```

A cheaper, permanent guard needs no external data: assert that `order_date` equals the IST date of `ordered_at`.

```sql
select order_id from {{ ref('stg_orders') }}
where order_date != cast(ordered_at + interval 330 minute as date)
```

This one fails on the current code and passes after the fix.
