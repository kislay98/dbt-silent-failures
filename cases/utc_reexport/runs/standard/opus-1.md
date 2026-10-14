claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 44 s | USD 0.219 | 2026-10-15 | project ad0b16b | prompt ef736a9770e3

# Order dates are bucketed by UTC day instead of India local day

## 1. Root cause

**File:** `models/staging/stg_orders.sql`, the line `cast(ordered_at as date) as order_date`.

**What's wrong in the data:** `raw_orders.ordered_at` is a plain `TIMESTAMP` with no time zone, and its values are in **UTC**. Order 73 shows this:

- In the warehouse it is `2018-03-15 19:45:20`.
- On the admin screen it is 16 March 01:15.
- The difference is exactly +5:30, which is India Standard Time.

Casting a UTC timestamp to a date gives the UTC day. Any order placed between 00:00 and 05:30 IST (18:30 to 24:00 UTC) therefore lands on the previous day in `stg_orders.order_date`. That error then flows into `orders` and `customers.first_order` / `most_recent_order`.

The symptoms fit this explanation:

- **Daily counts disagree but monthly totals agree.** Only a few orders shift by one day, and none of them crosses a month boundary.
- **`daily_revenue` matches the admin screen while `orders` doesn't.** `daily_revenue` is incremental and rebuilds only the last three days on each run. Its January to March rows were built when `order_date` still reflected the local day. The likely cause is that the feed or the staging model switched to the UTC `ordered_at` timestamp. The old rows were never recomputed, so they still hold the correct local-day values. Its most recent days, however, have been rebuilt with UTC dates and are also wrong now.
- **`schema.yml` documents `order_date` as "Date (UTC)".** That contradicts how the business counts days, which is by IST day.
- **No test checks dates.** All the existing tests are `unique`, `not_null` and `accepted_values`, so nothing looked at date semantics.

## 2. Fix

`models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is UTC; the business reports by India local day
        cast((ordered_at at time zone 'UTC') at time zone '{{ var("reporting_tz", "Asia/Kolkata") }}' as date) as order_date,
        status

    from source

)
```

How the conversion works in DuckDB:

- `ts AT TIME ZONE 'UTC'` reads the naive timestamp as UTC and returns a `TIMESTAMPTZ`.
- `... AT TIME ZONE 'Asia/Kolkata'` converts that back to an IST wall-clock timestamp.
- This uses DuckDB's ICU extension, which is autoloaded by default.
- If ICU isn't available, `cast(ordered_at + interval 330 minute as date)` gives the same result, because India has no daylight saving time.

Also update the `order_date`, `first_order` and `most_recent_order` descriptions in `models/schema.yml` from "Date (UTC)" to "Date (IST)".

Then rebuild everything, including the incremental model, so its recent UTC-dated rows are replaced:

```
dbt build --full-refresh
```

## 3. Tests that would have caught it

**Reconciliation test.** This would have failed on the first run after the switch, because `daily_revenue`'s history disagreed with `orders`. Create `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with expected as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(e.order_date, d.order_date) as order_date,
    e.orders as expected_orders, d.orders as actual_orders,
    e.revenue as expected_revenue, d.revenue as actual_revenue
from expected e
full outer join {{ ref('daily_revenue') }} d
    on e.order_date = d.order_date
where e.orders is distinct from d.orders
   or abs(coalesce(e.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

**Unit test that pins down the local-day rule.** It uses order 73 as the regression case and goes in `models/staging/schema.yml`:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_india_local_day
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: "2018-03-15 19:45:20", status: completed}  # 01:15 IST on 16th
          - {id: 1,  user_id: 1,  ordered_at: "2018-01-01 11:38:01", status: returned}   # same day either way
    expect:
      rows:
        - {order_id: 73, order_date: "2018-03-16"}
        - {order_id: 1,  order_date: "2018-01-01"}
```

One more thing to know: the `delete+insert` incremental model only deletes dates that appear in the new batch. If a date inside the three-day window ends up with no orders after a recompute, its stale row stays. The reconciliation test above would catch that case too.
