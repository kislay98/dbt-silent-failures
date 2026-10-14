claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 44 s | USD 0.008 | 2026-10-15 | project ad0b16b | prompt ef736a9770e3

## 1. Root cause

**The data is correct. The problem is that `stg_orders.sql` assigns the order day in UTC, but the business day is India local time (IST, UTC+05:30).**

- `raw_orders.ordered_at` is a naive `TIMESTAMP` in UTC. Order 73 is `2018-03-15 19:45:20` in the raw table. Adding 5h30m gives `2018-03-16 01:15:20` IST, which is the admin screen's value.
- `models/staging/stg_orders.sql` sets `order_date` with `cast(ordered_at as date)`, which truncates in UTC. Any order placed between 18:30 and 24:00 UTC lands on the previous IST day, and the same is true of its month on the last day of a month.
- `models/orders.sql` passes `order_date` through unchanged, so `orders` inherits the wrong day.
- `models/schema.yml` (orders, customers) and `models/customers.sql` describe these dates as "UTC". That is accurate for the current SQL, but it is not what ops needs.

The tests pass because nothing checks the day boundary. `unique`, `not_null`, and `accepted_values` do not notice a shifted date.

**Why `daily_revenue` matches the admin screen for January to March:** `models/daily_revenue.sql` is incremental with `delete+insert`, and it only rewrites rows from `max(order_date) - 3 days` onward. The max date is about 2018-04-04, so the rewrite window starts around 2018-04-01. Rows before that were written by an earlier version of the logic, which used local days, and have never been rebuilt. Only the recent rows reflect the current UTC logic.

The comment in `daily_revenue.sql` says the three-day window exists because payments land late. That is true for `orders.amount`, but the window is also why stale history survives. A change to how days are computed will never reach older rows unless the model is fully refreshed.

## 2. Fix

**`models/staging/stg_orders.sql`:** derive the day in IST and keep `ordered_at` as UTC.

```sql
        ordered_at,
        -- ordered_at is stored in UTC; the business day is India local time
        cast(timezone('Asia/Kolkata', timezone('UTC', ordered_at)) as date) as order_date,
        status
```

`timezone('UTC', ts)` treats the naive timestamp as UTC and returns a timestamptz. The outer call converts that to IST and returns a local timestamp, which is then cast to a date. This needs DuckDB's ICU extension for timezone names. dbt-duckdb normally loads it, but if you see a missing-extension error, load it explicitly.

**Docs (`models/schema.yml`, `models/customers.sql` descriptions):** change "Date (UTC)" to "Date (IST)" for `orders.order_date`, `customers.first_order`, and `customers.most_recent_order`. Also change the `daily_revenue` description to say days are IST.

**Rebuild the stale history:**

```
dbt build --select stg_orders+ --full-refresh
```

`--full-refresh` is needed for `daily_revenue`. Without it, the rows before the three-day window keep their old values. The `orders` and `customers` tables are rebuilt on every run anyway. Rebuilding derived tables is safe to repeat.

**Expected effect:** the example order moves to 16 March, and the daily counts for both windows should match the admin screen. Orders placed on the last day of a month between 18:30 and 24:00 UTC also move into the next month. Confirm this with ops, since the monthly totals they checked could change slightly.

## 3. Tests that would have caught this

**Unit test for the conversion.** This goes in `models/staging/schema.yml` and uses dbt's unit test syntax.

```yaml
unit_tests:
  - name: order_date_is_ist_day
    description: The business day is the IST calendar day, not the UTC day
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}
          - {id: 1, user_id: 1, ordered_at: '2018-01-01 11:38:01', status: returned}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
        - {order_id: 1, order_date: '2018-01-01'}
```

**Singular test for the stale rows.** The unit test cannot see `daily_revenue`'s incremental history. This test is in `tests/assert_daily_revenue_matches_orders.sql` and fails if any day disagrees with `orders`:

```sql
with expected as (

    select
        order_date,
        count(*) as orders,
        round(sum(amount), 2) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(actual.order_date, expected.order_date) as order_date
from {{ ref('daily_revenue') }} as actual
full outer join expected
    on actual.order_date = expected.order_date
where actual.orders is distinct from expected.orders
   or round(actual.revenue, 2) is distinct from expected.revenue
```

The unit test would have failed on the original `cast(ordered_at as date)`, and the singular test would have failed on the Jan–Mar rows in `daily_revenue` as soon as the logic changed.
