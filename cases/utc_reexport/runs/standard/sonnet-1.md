claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 23 s | USD 0.093 | 2026-10-15 | project ad0b16b | prompt ef736a9770e3

## 1. Root cause

**`models/staging/stg_orders.sql`** builds `order_date` with `cast(ordered_at as date)`. `raw_orders.ordered_at` is a naive `TIMESTAMP` holding **UTC**, so the cast gives the UTC calendar date. The shop and the admin screen use India local time (IST, UTC+05:30, no DST).

Order 73 shows the effect. Its raw `ordered_at` is `2018-03-15 19:45:20` UTC, which is `2018-03-16 01:15:20` IST. The admin screen shows the 16th and `orders` shows the 15th.

Any order placed between 18:30 and 24:00 UTC lands on the wrong day. That explains the small day-level differences in both windows. Monthly totals agree only because few orders fall across a month boundary in that 5.5-hour band.

All the tests pass because none of them checks which day an order falls on. `models/schema.yml` even documents `order_date` as "Date (UTC)", so the UTC bucketing looks deliberate to anyone reading the tests.

**Why `daily_revenue` matches the admin screen but not `orders`:** it is incremental (`delete+insert`) and only rebuilds the last 3 days, so older rows were never recomputed. The history Jan–Mar looks like it was built when `order_date` was still IST-based. After the UTC cast was introduced in `stg_orders`, only the most recent rebuilt days follow the UTC logic. This is inferred from the outputs. I did not see the earlier version of `stg_orders`.

Everything downstream of `stg_orders.order_date` is also shifted: `orders.order_date`, `customers.first_order` and `most_recent_order`.

## 2. Fix

In `stg_orders.sql`, convert from UTC to IST before taking the date:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is stored in UTC; the shop reports by India local day
        cast(ordered_at at time zone 'UTC' at time zone 'Asia/Kolkata' as date) as order_date,
        status

    from source
```

In DuckDB, `at time zone 'UTC'` turns the naive timestamp into a `TIMESTAMPTZ`, and the second `at time zone` gives the IST wall-clock time. This needs the ICU extension, which the Python DuckDB build bundles.

Then:
- Change the `order_date` and `first_order` / `most_recent_order` descriptions in `schema.yml` from "Date (UTC)" to "Date (Asia/Kolkata)".
- Rebuild `daily_revenue` from scratch, because the incremental run won't touch the stale history: `dbt build --full-refresh -s stg_orders+`.

## 3. Tests that would have caught it

**A unit test** on the timezone boundary, in `models/staging/schema.yml`:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_ist
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}
          - {id: 1,  user_id: 1,  ordered_at: '2018-03-15 18:29:59', status: completed}
    expect:
      rows:
        - {order_id: 73, order_date: 2018-03-16}
        - {order_id: 1,  order_date: 2018-03-15}
```

**A reconciliation test** that catches the stale incremental history: `tests/assert_daily_revenue_matches_orders.sql`

```sql
select
    coalesce(d.order_date, o.order_date) as order_date,
    d.orders as daily_orders,
    o.orders as orders_orders
from {{ ref('daily_revenue') }} d
full outer join (
    select order_date, count(*) as orders
    from {{ ref('orders') }}
    group by order_date
) o on d.order_date = o.order_date
where d.orders is distinct from o.orders
```

Against the current data this test would have failed on the dates where the two disagree. It would also catch any future drift between the incremental table and its source.
