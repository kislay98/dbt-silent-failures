claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 45 s | USD 0.166 | 2026-10-14 | project f769fdf+wk6ext | prompt 3ea8d8a27b63

## 1. Root cause

The ingestion job changed the shape of `raw_orders`, and `models/staging/stg_orders.sql` still reads the old column.

- **`ordered_at` is NULL from order 57 onward.** Order 57 is the first March order, so all 43 March and April orders have no `ordered_at` (35 + 8 = 43, matching the NULL row in the report).
- **The timestamp moved to a new column, `order_ts`.** `stg_orders` derives `order_date` from `cast(ordered_at as date)` and never reads `order_ts`, so `order_date` is NULL for those 43 orders.
- **`order_ts` is not the same clock on both sides of the cutover.**
  - Orders 1–56 have `order_ts = ordered_at`, which is shop local time (that's why `orders` matched the admin screen every day since January).
  - From order 57, `order_ts` is UTC. Order 73 has `order_ts` 2018-03-15 19:45:20, but the admin screen shows 2018-03-16 01:15:20. That is a +05:30 offset, and it changes the day.
  - So simply switching to `order_ts` would bring March back but put late-evening UTC orders on the wrong day, and the morning reconciliation would break.
- **Nothing failed because no test checks `order_date`.** None of the 32 tests covers it in `stg_orders` or `orders`.
- **`daily_revenue` looks fine only because it is incremental and stale.** Its March and April rows were written by earlier runs. This run's filter, `where order_date >= (select max(order_date) - interval 3 day ...)`, evaluates to NULL for the NULL-date rows, so they were dropped and nothing was deleted. It hid the problem instead of exposing it.
- **The same bug affects `customers`.** For example, customer 22's `most_recent_order` is 2018-02-17 but they placed order 58 on 2018-03-01. Customers 9, 12 and 93 have NULL `first_order`.
- **Amounts are unaffected.** 496 + 415 + 761 = 1672, so this is purely a date problem.
- **The docs are wrong too.** `schema.yml` describes `order_date` as "Date (UTC)", but it has always been shop local time.

## 2. Fix

In `models/staging/stg_orders.sql`, fall back to `order_ts` converted to local time only when `ordered_at` is missing:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ordered_at (shop local time) stopped being loaded from 2018-03-01; order_ts is UTC from then on
        coalesce(ordered_at, order_ts + interval '5 hours 30 minutes') as ordered_at,
        cast(coalesce(ordered_at, order_ts + interval '5 hours 30 minutes') as date) as order_date,
        status

    from source

)
```

- **Why `coalesce` and not `order_ts` for every row:** for orders 1–56, `order_ts` is already local time, so converting all of it would shift January and February.
- **Hard-coded offset:** +05:30 is inferred from order 73. Please confirm the shop's time zone. If it's a named zone, `timezone('Asia/Kolkata', order_ts at time zone 'UTC')` is the cleaner form; it uses DuckDB's ICU extension and handles DST if the zone has it.
- **Upstream:** ask the ingestion team whether `ordered_at` is deprecated, and whether `order_ts` is meant to be UTC for all rows. If they backfill the old rows to UTC, simplify this to a single conversion.

Then rebuild:

```
dbt build --full-refresh -s stg_orders+
```

The full refresh makes `daily_revenue` recompute from the corrected `orders` instead of keeping stale rows. Afterwards, check that:
- order 73 has `order_date = 2018-03-16`;
- `orders` by month shows 29 / 27 / 35 / 8;
- `daily_revenue` matches `orders` by month.

Also change the `order_date`, `first_order` and `most_recent_order` descriptions in `schema.yml` from "(UTC)" to shop local time.

## 3. Tests that would have caught it

**Not-null on `order_date`**, in `models/staging/schema.yml` under `stg_orders` and in `models/schema.yml` under `orders`:

```yaml
      - name: order_date
        tests:
          - not_null
```

Putting it on `stg_orders` makes the build fail before `orders`, `customers` and `daily_revenue` are rebuilt. This test alone would have failed last night with 43 rows.

**A unit test pinning the time zone behaviour.** A not-null test alone would pass with a naive `coalesce(ordered_at, order_ts)` that puts order 73 on the wrong day:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_shop_local
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 56, user_id: 79, ordered_at: '2018-02-28 10:39:12', status: completed, order_ts: '2018-02-28 10:39:12'}
          - {id: 73, user_id: 19, ordered_at: null, status: completed, order_ts: '2018-03-15 19:45:20'}
    expect:
      rows:
        - {order_id: 56, order_date: '2018-02-28'}
        - {order_id: 73, order_date: '2018-03-16'}
```
