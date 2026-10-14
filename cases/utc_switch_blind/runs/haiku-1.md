claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 43 s | USD 0.007 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**The warehouse and the admin screen use different day boundaries.** The admin screen shows IST (UTC+05:30). The warehouse takes the calendar date of `raw_orders.ordered_at` as-is, and that value is UTC.

The mismatch is in `jaffle_shop/models/staging/stg_orders.sql`:

```sql
cast(ordered_at as date) as order_date,
```

`raw_orders.ordered_at` is a naive `TIMESTAMP` with no zone. Casting it to `date` keeps the UTC date. Any order placed between 18:30:00 and 23:59:59 UTC is therefore counted on the previous IST day.

I checked this against the data:

- **23–24 March:** UTC timestamps for orders 78–83 are 07:27, 08:53, 08:56, 15:53, 23:44 on the 23rd and 01:35 on the 24th. Shifted by +05:30, that gives 4 orders on the 23rd and 2 on the 24th, which matches the admin screen exactly. The warehouse shows 5 and 1.
- **26–28 March:** the same shift reproduces the admin counts of 3, 2 and 1 for the 26th, 27th and 28th. Order 89 (18:45 UTC on the 27th) moves to the 28th, which is why the warehouse shows no 28th.
- **15/16 March:** the one order that is 1 day off is consistent with a late-evening UTC timestamp.
- **1–2 April:** the same pattern holds.

Every disagreement is explained by this shift. The monthly totals match because the shift only moves orders between days within the same month, except at month edges.

The tests pass because no test defines what the day boundary should be. `schema.yml` documents `order_date` as "Date (UTC)", so the code matches its docs. The docs are what's wrong for ops.

**What I can't tell from these files** is why the two agreed before mid-March. Either the feed's `ordered_at` convention changed around then, or the admin side did. To find out, compare the hour-of-day distribution of `ordered_at` before and after 15 March. A stored-clock change would show as a consistent 5.5-hour offset in the orders' times.

## 2. Fix

Assuming ops' definition of a day is IST (which the admin screen and the reconciliation both use), make the conversion explicit in staging:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is a naive UTC timestamp; ops reconcile on India local days
        cast(ordered_at at time zone 'UTC' at time zone 'Asia/Kolkata' as date) as order_date,
        status

    from source

)
```

Then:

- **Update the descriptions** in `models/schema.yml` for `orders.order_date` and `customers.first_order` / `most_recent_order` from "(UTC)" to "(IST)", since they change meaning.
- **Fully rebuild the incremental model.** `daily_revenue` only rebuilds the last 3 days on each run, so history won't correct itself:

  ```
  dbt build --full-refresh -s stg_orders+
  ```

If ops actually wants UTC days, the fix is the opposite: keep the code and change the admin reconciliation. Confirm which definition is the business one before you change anything.

## 3. A test that would have caught it

A **unit test** on `stg_orders` pins the day boundary. Add it to `models/staging/schema.yml`:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_local_day
    description: An order placed late evening UTC belongs to the next IST day.
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 1, user_id: 1, ordered_at: '2018-03-23 23:44:48', status: completed}
          - {id: 2, user_id: 1, ordered_at: '2018-03-23 07:27:20', status: completed}
    expect:
      rows:
        - {order_id: 1, order_date: '2018-03-24'}
        - {order_id: 2, order_date: '2018-03-23'}
```

This fails on the current code (order 1 comes out as 2018-03-23) and passes after the fix.

For an end-to-end guard, add a singular test in `tests/` that compares `orders` daily counts against a seed built from the admin screen's export. It fails on any day that disagrees, which catches problems the unit test can't, such as a future change in the feed's timestamp convention.
