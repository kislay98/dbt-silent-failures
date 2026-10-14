claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 44 s | USD 0.214 | 2026-10-15 | project ad0b16b | prompt ef736a9770e3

## 1. Root cause

**File: `models/staging/stg_orders.sql`**

```sql
cast(ordered_at as date) as order_date,
```

`raw_orders.ordered_at` is a naive `TIMESTAMP` that holds **UTC**. Casting it straight to `date` gives the **UTC** calendar day. The shop's business day, and the admin screen, is **India time (IST, UTC+05:30)**. Any order placed between 18:30 and 23:59 UTC belongs to the *next* day in India, but the warehouse files it under the previous day.

Order 73 shows this exactly:

| | value |
|---|---|
| `raw_orders.ordered_at` | `2018-03-15 19:45:20` (UTC) |
| + 5h30 → IST | `2018-03-16 01:15:20`, which matches the admin screen |
| `stg_orders.order_date` / `orders.order_date` | `2018-03-15` (wrong) |

Order 10 behaves the same way: `2018-01-13 19:35:47` UTC is 14 Jan in IST, and the admin screen shows 2 orders on the 14th. Order 92 (`2018-04-01 20:55:29`) is also misdated; it belongs on 2 April.

This explains each symptom:

- **Monthly totals agree.** Only orders in the 18:30–24:00 UTC window shift, and each moves by one day. None of them happens to cross a month boundary in this data.
- **All tests pass.** No test checks what day `order_date` means. `unique`, `not_null` and `accepted_values` are all still true.
- **`daily_revenue` matches the admin screen but not `orders`.** `daily_revenue` is incremental (`delete+insert`) and rebuilds only the last 3 days on each run. Its January–March rows were written while `order_date` still came out as the India-local day. Most likely the ingestion job used to land `ordered_at` in IST and now lands it in UTC; it could also be that the staging model changed. Since then, only the trailing 3-day window has been rewritten with the wrong dates. The history in `daily_revenue` is stale but correct, and `orders` (a full `table`) is recomputed every night and is wrong everywhere. It is worth confirming with the ingestion team when `ordered_at` switched to UTC.
- **Documentation.** `models/schema.yml` documents `order_date`, `first_order` and `most_recent_order` as "Date (UTC)", which is not the business day ops reports on.

Affected downstream models: `orders.order_date`, `customers.first_order` / `most_recent_order`, and the recent rows of `daily_revenue`.

## 2. Fix

`models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is UTC; the business day is India local time
        cast((ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata' as date) as order_date,
        status

    from source

)
```

In DuckDB, the first `at time zone` reads the naive value as UTC and returns a `timestamptz`. The second converts it to naive Asia/Kolkata wall-clock time. This relies on the ICU extension, which ships with standard DuckDB builds. If ICU isn't available, `cast(ordered_at + interval '5 hours 30 minutes' as date)` is exact because India has no DST.

Update `models/schema.yml` so `order_date`, `first_order` and `most_recent_order` say "Date (IST, Asia/Kolkata)" instead of "Date (UTC)".

Then rebuild `daily_revenue` from scratch. With `delete+insert`, any date keys written with the wrong dates in the last windows could otherwise linger:

```
dbt build --full-refresh --select stg_orders+
```

## 3. A test that would have caught it

**a) Unit test on the timezone logic** (dbt ≥ 1.8), using order 73 as the fixture. Add to `models/staging/schema.yml`:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_india_local_day
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: "2018-03-15 19:45:20", status: completed}
          - {id: 25, user_id: 51, ordered_at: "2018-01-28 03:30:52", status: completed}
    expect:
      rows:
        - {order_id: 73, order_date: "2018-03-16"}
        - {order_id: 25, order_date: "2018-01-28"}
```

This fails on the current `cast(ordered_at as date)`.

**b) Reconciliation test between `orders` and `daily_revenue`.** This would have caught the drift as soon as the incremental history and the full-rebuild table disagreed. Create `tests/daily_revenue_matches_orders.sql`:

```sql
with from_orders as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select coalesce(o.order_date, d.order_date) as order_date,
       o.orders as orders_in_orders, d.orders as orders_in_daily_revenue,
       o.revenue as revenue_in_orders, d.revenue as revenue_in_daily_revenue
from from_orders o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.01
```

On today's data this returns the shifted dates, such as 15 and 16 March. Test (a) pins the meaning of `order_date`. Test (b) catches any future case where an incremental model silently diverges from its source.
