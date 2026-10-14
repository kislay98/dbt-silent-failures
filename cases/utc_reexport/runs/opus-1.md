claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 50 s | USD 0.184 | 2026-10-14 | project f769fdf | prompt 9aa546ec9ab1

# dbt order-count diagnosis: daily dates are taken in UTC, not India time

## 1. Root cause

**File:** `models/staging/stg_orders.sql`, the line `cast(ordered_at as date) as order_date`.

**What's wrong in the data:** `raw_orders.ordered_at` is a plain `TIMESTAMP` with no time zone, and its values are UTC wall-clock times. Casting it to `date` gives the UTC calendar day. The admin screen uses India time (UTC+5:30). Any order placed between 18:30 and 23:59 UTC therefore lands on the previous day in the warehouse.

Your sample confirms this exactly:

| order | `ordered_at` (UTC) | IST | warehouse day | admin day |
|---|---|---|---|---|
| 73 | 03-15 19:45:20 | **03-16 01:15:20** | 15th | 16th ✔ (ops' example) |
| 16 | 01-17 21:07:25 | 01-18 02:37 | 17th | 18th |
| 82 | 03-23 23:44:48 | 03-24 05:14 | 23rd | 24th |
| 87, 88 | 03-26 21:12 / 21:51 | 03-27 02:42 / 03:21 | 26th | 27th |
| 89 | 03-27 18:45:59 | 03-28 00:15 | 27th | 28th |
| 12, 1, 99 | before 18:30 UTC | same day | unchanged | unchanged |

Moving these orders forward one day reproduces every admin count in both windows. For example:
- Mar 26: 5 → 3
- Mar 27: 1 − 1 + 2 = 2
- Mar 28: 0 → 1
- Jan 6 → 7, Jan 10 → 11, Jan 13 → 14, Jan 17 → 18

Monthly totals agree because only orders at the edge of a month could cross into another month, and those happen to balance out.

**Why `daily_revenue` is right but `orders` is wrong:** `daily_revenue` is incremental and only rebuilds the last three days. Its Jan–Mar rows were written when `order_date` still came out in IST. The data before the change can't tell us what it was. Possibly `ordered_at` used to arrive as `TIMESTAMPTZ`, which DuckDB casts to a date in the session's local zone, or as IST wall-clock time. After the ingestion job started loading naive UTC timestamps, only the full-rebuild tables (`orders`, `customers`) switched to UTC days.

**Why the tests didn't catch it:** every test is `unique`, `not_null`, `accepted_values` or `relationships`, and a shifted date passes all of them. Note that `schema.yml` even documents `order_date` as "Date (UTC)", which is not what the business uses.

## 2. Fix

`models/staging/stg_orders.sql`:

```sql
        ordered_at,
        -- ordered_at is UTC; the business day is India local time
        cast((ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata' as date) as order_date,
```

This needs DuckDB's ICU extension, which is bundled in standard builds. India has no daylight saving, so `cast(ordered_at + interval '5 hours 30 minutes' as date)` is an equivalent fallback if ICU isn't available.

Then:

1. In `models/schema.yml`, change the `order_date`, `first_order` and `most_recent_order` descriptions from "Date (UTC)" to "Date (India local time, Asia/Kolkata)".
2. Rebuild with a full refresh so the incremental model's recent window, which was written with UTC days, is also corrected:
   ```
   dbt build --full-refresh -s stg_orders+
   ```
   This also fixes `customers.first_order` and `most_recent_order`, which had the same bug.
3. Ask the ingestion owners to confirm the zone of `ordered_at` and keep it stable. Ideally they would load it as `TIMESTAMPTZ` so the zone is explicit.

## 3. Tests that would have caught it

**a) Unit test** (dbt ≥ 1.8). Add this to `models/staging/schema.yml`. It pins the conversion using ops' real example plus a near-boundary case:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_india_local_day
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}
          - {id: 99, user_id: 85, ordered_at: '2018-04-09 18:05:08', status: placed}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}   # 01:15 IST next day
        - {order_id: 99, order_date: '2018-04-09'}   # 23:35 IST same day
```

**b) Reconciliation test** `tests/assert_daily_revenue_matches_orders.sql`. This test would have failed on the very first build after the ingestion change:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
)
select coalesce(o.order_date, d.order_date) as order_date,
       o.orders as orders_count, d.orders as daily_revenue_count
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or o.revenue is distinct from d.revenue
```

The unit test catches the timezone logic directly. The reconciliation test catches the more general problem of the incremental table and its source drifting apart for any reason.
