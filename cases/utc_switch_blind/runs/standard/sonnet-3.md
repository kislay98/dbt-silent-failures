claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 25 s | USD 0.097 | 2026-10-15 | project ad0b16b | prompt 9fc7feb7aef0

## 1. Root cause

**`models/staging/stg_orders.sql`** derives the reporting date with `cast(ordered_at as date)`. `raw_orders.ordered_at` is a naive `TIMESTAMP` that is documented as UTC (`models/schema.yml` says "Date (UTC)"). `orders.sql` and `daily_revenue.sql` inherit that date unchanged.

The admin screen buckets by India local time (IST, UTC+5:30). Any order placed between 18:30 and 23:59:59 UTC belongs to the next local day, but dbt files it under the UTC day. For example:
- Order 88 (`2018-03-26 21:51:20` UTC) is 03-27 in IST, but `orders` has it on 03-26.
- Order 73 (`2018-03-15 19:45:20` UTC) is 03-16 in IST.

This is why the monthly totals and `sum(amount)` (1672.00) still match: orders only shift by a day, and only a few at a month boundary move across months. It is also why all 44 steps pass, because no test compares the date to the local calendar.

Caveat: from the sample alone I can't explain why it only started in mid-March. Order 28 (`2018-01-29 22:50:40`) would already have been off by a day. The likely explanations are that the Jan–Feb rows are stored in local time and the feed switched to UTC around mid-March, or that nobody reconciled those earlier days. Before applying the fix, check this against the seed:

```sql
select date_trunc('week', ordered_at) wk,
       count(*) filter (where ordered_at::time >= time '18:30') as late_utc_orders
from raw_orders group by 1 order by 1;
```

If the earlier rows are already local, apply the conversion only to rows from the cutover onward (or fix the loader).

## 2. Fix

In `stg_orders.sql`, convert to the shop's time zone before taking the date, and keep the UTC column too:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at at time zone 'UTC' at time zone 'Asia/Kolkata' as date) as order_date,
        status
```

Update the `order_date`, `first_order` and `most_recent_order` descriptions in `schema.yml` from "UTC" to "India local date (Asia/Kolkata)".

Then run `dbt build --full-refresh`. `daily_revenue` only rebuilds the last 3 days incrementally, so the older rows would otherwise keep the wrong dates.

## 3. Test

First, a singular test that pins the definition, `tests/assert_order_date_is_local.sql`:

```sql
select order_id, ordered_at, order_date
from {{ ref('stg_orders') }}
where order_date != cast(ordered_at at time zone 'UTC' at time zone 'Asia/Kolkata' as date)
```

This only guards against someone changing the definition. To catch a mismatch with the source of truth, add the admin screen's daily counts as a seed (`seeds/admin_daily_orders.csv` with `day,orders`) and a reconciliation test, `tests/assert_daily_orders_match_admin.sql`:

```sql
select coalesce(a.day, d.order_date) as day, a.orders as admin_orders, d.orders as dbt_orders
from {{ ref('admin_daily_orders') }} a
full outer join {{ ref('daily_revenue') }} d on a.day = d.order_date
where a.day >= date '2018-03-01'
   and a.orders is distinct from d.orders
```

Without a date bound, `daily_revenue` days that predate the admin seed would show up as false mismatches. In production, ops can refresh that seed from the admin export.
