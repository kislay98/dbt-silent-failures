claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 69 s | USD 0.009 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**The shift is a timezone mismatch.** `stg_orders.sql` sets `order_date` with `cast(ordered_at as date)`, which takes the calendar date of `ordered_at` as stored. The docs in `models/schema.yml` say that date is UTC. Ops reconcile against the admin screen, which shows India time (UTC+05:30). An order placed at or after 18:30 UTC is already the next day in India, so it lands on the wrong day in `orders` and everything downstream.

Every disagreeing day in the table is explained by orders with a UTC time of 18:30 or later moving to the next day:

| Admin day | Warehouse day | Explanation |
|---|---|---|
| 03-16 | 03-15 | order 15 Mar ≥18:30 UTC |
| 03-24 | 03-23 | order 82, `23:44:48` UTC → 24 Mar 05:14 IST |
| 03-27 | 03-26 | orders 87, 88 (`21:12`, `21:51` UTC) → 27 Mar 02:42 and 03:21 IST |
| 03-28 | 03-27 | order 89, `18:45:59` UTC → 28 Mar 00:15 IST |
| 04-02 | 04-01 | order ≥18:30 UTC on 1 Apr |
| 04-03 | 04-02 | order ≥18:30 UTC on 2 Apr |

The counts reconcile exactly, and the monthly totals match because no shift crosses a month boundary. Orders before mid-March that fall after 18:30 UTC would also shift, but none show up. With roughly a quarter of orders placed after 18:30 UTC, about 25 early-March orders with zero shifts is very unlikely if they were stored as UTC. So the early rows are most likely stored as India local time, and from about 15 March `raw_orders.ordered_at` switched to UTC. The raw column is a naive `TIMESTAMP` with no offset, so the warehouse cannot tell which convention a row uses.

**Files involved:**
- `models/staging/stg_orders.sql`: the `order_date` derivation assumes UTC.
- `models/schema.yml` and `models/staging/schema.yml`: the "Date (UTC)" descriptions for `orders.order_date`, `customers.first_order`, and `customers.most_recent_order`.
- The `raw_orders` seed CSV (not included). This is where the mixed conventions actually come from.

**Verify before changing anything:** ask ops for the admin timestamp of order 82. If admin shows `2018-03-24 05:14:48`, `ordered_at` is UTC for that row. Then check an early-March order placed after 18:30: if admin shows the same clock time as `ordered_at`, early rows are IST.

## 2. Fix

Store one convention, UTC, and convert once in staging. That requires normalising the pre-cutover rows in the raw data, which I'd do at the source or seed, subtracting 5h30m from rows loaded before the cutover (about 15 March, to be confirmed from the verification above).

Then change staging to convert explicitly. India has no DST, so a fixed offset is safe:

```sql
-- models/staging/stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is UTC; the business reconciles on India time (UTC+05:30, no DST)
        cast(ordered_at + interval '5 hours 30 minutes' as date) as order_date,
        status

    from source

)
```

Update the descriptions in `models/schema.yml` to say "Date (IST)", and do the same for `customers.first_order` and `customers.most_recent_order`.

**Rebuild, including the incremental model.** `daily_revenue` only rebuilds the last three days, so historical days would stay wrong:

```
dbt build --select stg_orders+ --full-refresh
```

If you'd rather not change the raw data, the alternative is a `CASE` in staging that applies the offset only to rows before the cutover. I'd avoid that because it hard-codes a date.

## 3. Test that would have caught it

The existing tests only check unique, not-null, and accepted values, and a wrong date passes all of them. Two tests would catch this:

**a) Reconciliation against the admin screen (catches data drift).** Export the admin daily counts into a seed and compare:

```sql
-- tests/assert_daily_orders_match_admin.sql
select
    a.order_date,
    a.orders as admin_orders,
    coalesce(d.orders, 0) as warehouse_orders
from {{ ref('admin_daily_order_counts') }} a
left join {{ ref('daily_revenue') }} d
    on a.order_date = d.order_date
where coalesce(d.orders, 0) <> a.orders
```

**b) Unit test for the day boundary (catches the logic).** This pins the 18:30 UTC boundary:

```yaml
# models/staging/schema.yml
unit_tests:
  - name: order_date_is_india_day
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 1, user_id: 1, ordered_at: '2018-03-23 18:29:59', status: completed}
          - {id: 2, user_id: 1, ordered_at: '2018-03-23 18:30:00', status: completed}
          - {id: 3, user_id: 1, ordered_at: '2018-03-23 23:44:48', status: completed}
    expect:
      rows:
        - {order_id: 1, order_date: '2018-03-23'}
        - {order_id: 2, order_date: '2018-03-24'}
        - {order_id: 3, order_date: '2018-03-24'}
```

The unit test checks the logic and the reconciliation test checks the data. Only the reconciliation test would have flagged the mid-March data change.

**Other issue I noticed (not the cause here):** `region_revenue` joins `raw_customer_regions`, which has an `updated_at` column, but there is no uniqueness test on `customer_id`. If a customer has more than one region row, their revenue is double-counted. The `unique` test on `region_revenue.region` doesn't catch that, because `region` is the group-by key.
