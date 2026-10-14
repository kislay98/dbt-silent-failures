claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 24 s | USD 0.072 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`models/staging/stg_orders.sql`** derives the date with `cast(ordered_at as date)`.

`raw_orders.ordered_at` is a naive `TIMESTAMP` that is evidently stored in UTC. The cast takes the UTC calendar date, but ops reconcile against the admin screen, which uses India local time (IST, UTC+5:30). Any order placed between 18:30 and 24:00 UTC is already the next day in India, so it lands on the wrong `order_date`.

The data confirms this:
- **Order 82:** `2018-03-23 23:44:48` UTC is 03-24 05:14 IST. The admin screen has 03-23 = 4 and 03-24 = 2, against our 5 and 1.
- **Orders 87 and 88:** `03-26 21:12` and `21:51` UTC are 03-27 IST.
- **Order 89:** `03-27 18:45:59` UTC is 03-28 00:15 IST. The admin screen has 03-27 = 2 and 03-28 = 1, against our 03-26 = 5 and 03-27 = 1.
- **03-15 vs 03-16:** our 03-15 order belongs to the admin's 03-16, which is why 03-15 shows up in our output and not in the admin list.
- **04-01:** the same shift explains the 04-01 and 04-02/03 differences.

It only started in mid-March because earlier orders happened to fall before 18:30 UTC. Monthly totals and `sum(amount)` match because shifts only cross day boundaries, and only rarely cross a month boundary. Everything downstream (`orders`, `customers.first_order`/`most_recent_order`, `daily_revenue`) inherits the wrong date.

The tests pass because nothing tests the date logic. The `schema.yml` descriptions also say "Date (UTC)", so the model is arguably doing what it documents. The requirement is local date.

## 2. Fix

In `stg_orders.sql`, convert to IST before casting. India has no DST, so this is safe:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast((ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata' as date) as order_date,
        status
```

`ordered_at at time zone 'UTC'` makes it a `TIMESTAMPTZ`, and the second `at time zone` turns it into IST wall-clock time. `ordered_at + interval 330 minute` is an equivalent alternative that doesn't need the ICU extension.

Also update the `schema.yml` descriptions of `order_date`, `first_order` and `most_recent_order` from "(UTC)" to "(IST)".

`daily_revenue` is incremental and only rebuilds the last 3 days, so older rows keep the wrong dates. Run:

```
dbt build --full-refresh
```

This assumes the raw timestamps really are UTC. The data is consistent with that, but confirm with whoever owns the feed.

## 3. Tests that would have caught it

**A unit test** (dbt ≥1.8) in `models/staging/schema.yml`, pinning the boundary cases:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_ist
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 1, user_id: 1, ordered_at: '2018-03-23 18:29:59', status: placed}
          - {id: 2, user_id: 1, ordered_at: '2018-03-23 18:30:00', status: placed}
          - {id: 3, user_id: 1, ordered_at: '2018-03-23 23:44:48', status: placed}
    expect:
      rows:
        - {order_id: 1, order_date: '2018-03-23'}
        - {order_id: 2, order_date: '2018-03-24'}
        - {order_id: 3, order_date: '2018-03-24'}
```

(You may need to list the other columns in the expected rows, or use the default `format: dict`, which only compares the columns you give.)

**A reconciliation test.** Load the admin screen's daily counts as a seed, `seeds/admin_daily_counts.csv` with columns `day,orders`, and add `tests/assert_daily_orders_match_admin.sql`:

```sql
select a.day, a.orders as admin_orders, coalesce(o.orders, 0) as dbt_orders
from {{ ref('admin_daily_counts') }} a
left join (
    select order_date, count(*) as orders from {{ ref('orders') }} group by 1
) o on o.order_date = a.day
where a.orders != coalesce(o.orders, 0)
```

This is the check ops do by hand each morning, now run on every build. Either test fails on the current code.
