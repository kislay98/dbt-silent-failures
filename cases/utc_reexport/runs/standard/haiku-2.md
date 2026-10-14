claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 32 s | USD 0.007 | 2026-10-15 | project ad0b16b | prompt ef736a9770e3

## 1. Root cause

**`models/staging/stg_orders.sql`** derives the order day in UTC:

```sql
ordered_at,
cast(ordered_at as date) as order_date,
```

`raw_orders.ordered_at` is a UTC timestamp, as the `Date (UTC)` description in `schema.yml` says. The admin screen shows the local day in India (UTC+05:30), so an order placed between 18:30 and 23:59 UTC lands on the next admin day. Order 73 shows this:

- `raw_orders.ordered_at` = `2018-03-15 19:45:20` (UTC)
- Local time = 19:45:20 + 5:30 = `2018-03-16 01:15:20` IST, which matches the admin screen.
- `stg_orders.order_date` = `2018-03-15`, so `orders` and everything downstream is one day early.

The disagreements are exactly the orders in the UTC 18:30–24:00 band, which is why they show up on scattered days. Monthly totals agree because the shift only moves orders across a month boundary when one falls on the last day of the month, so the monthly sums are unaffected.

`models/daily_revenue.sql` groups by `orders.order_date`, so it inherits the same UTC day. Its Jan–Mar rows should therefore disagree with `orders` as well, but the report says they match the admin screen. That can only happen if those historical rows were written under an earlier definition and never rebuilt. The incremental filter explains why: it only rebuilds from `max(order_date) - 3 days` onward, and the data ends on 2018-04-06, so only roughly 3–6 April is refreshed on each run. Jan–Mar rows are frozen. I have not verified this against the warehouse. The check is to compare `daily_revenue` with `select order_date, count(*), sum(amount) from orders group by 1` for 5–18 Jan and 20–31 Mar. I expect those windows to differ.

**Why the tests pass:** `unique`, `not_null` and `accepted_values` say nothing about which calendar day an order belongs to. A wrong date still passes every one of them.

## 2. Fix

Make the staging model produce the local (IST) day. India has no DST, so a fixed offset is correct:

```sql
-- models/staging/stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is UTC; the shop works in India time (UTC+05:30, no DST)
        cast(ordered_at + interval '5 hours 30 minutes' as date) as order_date,
        status

    from source

)
```

Then:

- Update the `order_date` description in `models/schema.yml` from `Date (UTC)` to `Date (IST)`. The same applies to `first_order` and `most_recent_order` in `customers`, since they derive from `order_date` and will also move.
- Rebuild the frozen history. A normal run will not fix Jan–Mar in `daily_revenue`:

```
dbt build --full-refresh -s stg_orders+
```

- Reconcile `daily_revenue` against the admin screen for both windows before closing the ticket.

If finance needs UTC days anywhere, keep `stg_orders.order_date` as UTC and compute the local day only in `daily_revenue`. Since ops compares against the admin screen, I'd use IST in staging so every model agrees with it.

## 3. Test that would have caught this

A dbt unit test on `stg_orders` with fixed inputs. It fails on the current code (order 73 comes out as 15 March) and checks the day boundary on both sides of 18:30 UTC:

```yaml
# models/staging/schema.yml
unit_tests:
  - name: stg_orders_order_date_is_india_local_day
    description: ordered_at is UTC; order_date must be the IST calendar day
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}  # 01:15 IST, 16 Mar
          - {id: 74, user_id: 19, ordered_at: '2018-03-15 18:29:59', status: completed}  # 23:59:59 IST, 15 Mar
          - {id: 75, user_id: 19, ordered_at: '2018-03-15 18:30:00', status: completed}  # 00:00 IST, 16 Mar
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
        - {order_id: 74, order_date: '2018-03-15'}
        - {order_id: 75, order_date: '2018-03-16'}
```

I have not run this or the fix. The unit test should be run with `dbt test -s stg_orders` before the full rebuild, and it should fail against the current `stg_orders.sql`.
