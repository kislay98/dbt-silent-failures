claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 31 s | USD 0.078 | 2026-10-14 | project bef6b69 | prompt f253c60e27c9

## 1. Root cause

**`models/staging/stg_orders.sql`**: `cast(ordered_at as date) as order_date` takes the calendar date of a timestamp that is in UTC. `raw_orders.ordered_at` is a naive `TIMESTAMP` and the schema docs say it is UTC. The admin screen shows India local time (IST, UTC+05:30). Any order placed from 18:30 to 23:59 UTC falls on the next calendar day in India, but the warehouse files it under the UTC day.

The data bears this out:
- Order 73 is `2018-03-15 19:45:20` UTC, which is 16 March 01:15 IST. The admin screen and warehouse disagree exactly as ops saw.
- Order 16 (17 Jan 21:07 UTC) moves to the 18th. Order 82 (23 Mar 23:44) moves to the 24th. Orders 87 and 88 (26 Mar 21:12 and 21:51) move to the 27th. Order 89 (27 Mar 18:45) moves to the 28th.
- Applying the shift reproduces the admin counts. For 23 to 28 March it gives 4, 2, 3, 2, 1, which matches admin.
- Monthly totals agree because only orders near a day boundary move, and almost all of them stay inside the same month.

Every model reads `stg_orders.order_date`, so `orders`, `customers.first_order` and `most_recent_order`, and `daily_revenue` all inherit the UTC date. The 29 tests pass because none of them checks which time zone the day is in.

**`models/daily_revenue.sql`** is why the dashboard disagrees with `orders`. It is incremental and only rebuilds the last three days, so older rows are never recomputed. Its Jan to Mar rows have IST dates while `orders` has UTC dates. The diff rows that exist only in the dashboard (`order_date` NULL on the warehouse side) are IST days that `orders` has no row for. I can't see the run history, so this is inferred: those rows were built under different date logic than `orders` now uses, and nothing since has refreshed them.

## 2. Fix

In `stg_orders.sql`, convert to IST before taking the date:

```sql
        ordered_at,
        cast(timezone('Asia/Kolkata', ordered_at at time zone 'UTC') as date) as order_date,
```

In DuckDB, `ordered_at at time zone 'UTC'` treats the naive timestamp as UTC and returns a `TIMESTAMPTZ`. `timezone('Asia/Kolkata', ...)` then returns the local wall-clock time. This needs the ICU extension, which the standard DuckDB build bundles. If you'd rather avoid it, `ordered_at + interval 330 minute` is equivalent because IST has no DST.

Then:
- Rebuild the incremental table, because the normal run only touches the last three days: `dbt build --full-refresh -s stg_orders+` (or at least `dbt run --full-refresh -s daily_revenue` after `orders` has rebuilt).
- Update the `order_date`, `first_order` and `most_recent_order` descriptions in `schema.yml` from "Date (UTC)" to "Date (IST)".
- Optionally, make the time zone a var such as `var('shop_timezone', 'Asia/Kolkata')`.

I haven't run any of this.

## 3. Tests that would have caught it

**a) A unit test (dbt 1.8+) pinning the boundary behaviour.** Add it to `models/staging/schema.yml`:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_ist
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}
          - {id: 12, user_id: 59, ordered_at: '2018-01-15 10:13:37', status: completed}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
        - {order_id: 12, order_date: '2018-01-15'}
```

This fails on the current code because order 73 comes back as 15 March.

**b) A reconciliation test for the stale incremental table.** Save it as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as dashboard_orders
from (select order_date, count(*) as orders from {{ ref('orders') }} group by 1) o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
```

This returns rows (a failure) whenever the incremental model has drifted from `orders`. It would have flagged the stale Jan to Mar rows immediately.
