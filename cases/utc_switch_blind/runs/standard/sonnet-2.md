claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 25 s | USD 0.096 | 2026-10-15 | project ad0b16b | prompt 9fc7feb7aef0

I couldn't see the seed rows, so this rests on the sample data and your admin counts, not a full comparison.

## 1. Root cause

`models/staging/stg_orders.sql` derives the date with `cast(ordered_at as date)`. `raw_orders.ordered_at` is a naive `TIMESTAMP`, apparently stored in UTC, so any order placed after 18:30 UTC (00:00 IST) lands on the previous calendar day in `orders.order_date`. `orders` and `daily_revenue` inherit this, and `schema.yml` even documents the column as "Date (UTC)".

The sample matches your admin table:
- Order 73 is at `2018-03-15 19:45:20`, which is 01:15 IST on 03-16. Admin shows no orders on 03-15 and one on 03-16, while the model puts it on 03-15.
- Order 88 is at `2018-03-26 21:51:20`, which is 03-27 in India. Admin 03-27 has 2 orders.
- Order 92 is at `2018-04-01 20:55:29`, which is 04-02 in India. Admin 04-02 has 1 order.

Daily counts shift, but monthly totals and `sum(amount)` stay the same, because no order moves across a month boundary and nothing is dropped. Every dbt test passes because none of them checks the date logic.

I can't tell why it only started in mid-March. Either early rows happened to have no late-evening orders, or the feed switched from local time to UTC around then. Check `min(ordered_at)` for the late-hours orders. If the feed did switch, apply the offset only from the cutover.

## 2. Fix

In `stg_orders.sql`, convert to IST before taking the date. India has no DST, so a fixed offset is safe and needs no ICU extension:

```sql
        ordered_at,
        cast(ordered_at + interval 330 minute as date) as order_date,
```

Then update the `order_date` descriptions in `schema.yml` (and `customers.first_order` and `most_recent_order`) from "Date (UTC)" to "Date (IST)".

`daily_revenue` only rebuilds the last 3 days, so the old wrong rows would remain. Rebuild it with:

```
dbt build --full-refresh -s stg_orders+
```

## 3. Test

A singular test, `tests/assert_order_date_is_ist.sql`, fails if any order's date isn't the IST date:

```sql
select o.order_id
from {{ ref('orders') }} o
join {{ ref('raw_orders') }} r on r.id = o.order_id
where o.order_date != cast(r.ordered_at + interval 330 minute as date)
```

This catches regressions but would share any wrong assumption about the source timezone. To catch the original problem, add a seed with the admin screen's daily counts (for example `seeds/expected_daily_orders.csv`) and reconcile it:

```sql
select coalesce(e.day, d.order_date) as day, e.orders as expected, d.orders as actual
from {{ ref('expected_daily_orders') }} e
full join {{ ref('daily_revenue') }} d on d.order_date = e.day
where d.order_date >= date '2018-03-01'
  and coalesce(e.orders, 0) != coalesce(d.orders, 0)
```
