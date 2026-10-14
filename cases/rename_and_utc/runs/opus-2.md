claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 52 s | USD 0.180 | 2026-10-14 | project f769fdf+wk6ext | prompt 3ea8d8a27b63

# Diagnosis: the orders report lost March and April because `stg_orders` reads a column the ingestion job stopped filling

## 1. Root cause

**The data:** `raw_orders.ordered_at` is NULL for every order from id 57 (2018-03-01) onward. The ingestion job now writes those timestamps to a new column, `order_ts`. Ids 57–99 are exactly the 43 orders with no date.

**The code:** `models/staging/stg_orders.sql` still builds the date only from the old column:

```sql
cast(ordered_at as date) as order_date,
```

It never reads `order_ts`. So `order_date` is NULL for all of March and April, and that flows into everything built on `stg_orders`.

**`order_ts` is not in the same time zone as `ordered_at`:**
- **Order 73:** `order_ts` is 2018-03-15 19:45:20, but the admin screen shows 2018-03-16 01:15:20. That is a +05:30 gap, so the new `order_ts` values are UTC, while the admin screen shows shop-local time.
- **Older rows:** for ids 54–56, `order_ts` equals `ordered_at`. Those rows look like a copy of the old column.
- **Old dates were local:** `ordered_at`-based dates matched the admin screen every day since January, so they were local time. The schema descriptions saying "Date (UTC)" are wrong.

Simply switching to `cast(order_ts as date)` would push late-evening orders onto the wrong day (order 73 would land on 03-15).

**Why nothing failed:**
- **No test on the date:** nothing tests `order_date` for not-null.
- **`daily_revenue` hid the gap:** its incremental filter `where order_date >= (select max(order_date) - interval 3 day ...)` evaluates to NULL for NULL-date rows, so they were silently skipped. Its March and April rows are left over from earlier runs, before `ordered_at` went NULL. That is why it still looks right.
- **Totals still match:** `sum(amount)` is 1672 in both because payments are unaffected. Only the dates are missing.
- **`customers` is also wrong, and it fails quietly.** `min` and `max` ignore NULLs:
  - Customers 9, 12 and 93 have NULL first and most-recent order dates.
  - Customer 22's `most_recent_order` should be 2018-03-01 (order 58), but shows 2018-02-17.
  - Customer 8 has 2 orders but only one date counted.

## 2. Fix

In `models/staging/stg_orders.sql`:
- prefer `ordered_at`, so January and February stay exactly as they reconciled;
- otherwise convert `order_ts` from UTC to shop-local time.

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ordered_at is shop-local; the ingestion job now fills order_ts (UTC) instead
        coalesce(
            ordered_at,
            timezone('{{ var("shop_timezone") }}', timezone('UTC', order_ts))
        ) as ordered_at,
        cast(coalesce(
            ordered_at,
            timezone('{{ var("shop_timezone") }}', timezone('UTC', order_ts))
        ) as date) as order_date,
        status

    from source

)
```

In `dbt_project.yml`:

```yaml
vars:
  shop_timezone: 'Asia/Kolkata'   # +05:30, inferred from order 73; confirm with ops
```

**Confirm the time zone first.** +05:30 comes from a single data point, order 73. Ask ops or the app team which zone the shop uses before relying on it. If ICU time zones aren't available in your DuckDB build, use `order_ts + interval '5 hours 30 minutes'` instead (no DST at +05:30).

Also ask the ingestion team whether `ordered_at` is permanently gone. If it is, the `coalesce` keeps working, but you should eventually switch to `order_ts` alone. For that you need to know what `order_ts` means for rows before March, since for ids 54–56 it appears to be the local value, not UTC.

**Rebuild everything, including `daily_revenue`:**

```bash
dbt build --full-refresh -s stg_orders+
```

The full refresh makes `daily_revenue` come from current data instead of leftover rows. Afterwards, check that:
- `orders` by month gives 29 / 27 / 35 / 8 with nothing NULL;
- order 73 has `order_date` 2018-03-16.

**Fix the docs:** in `models/schema.yml`, change the `order_date`, `first_order` and `most_recent_order` descriptions from "Date (UTC)" to shop-local date.

## 3. Tests that would have caught this

`models/staging/schema.yml` and `models/schema.yml`:

```yaml
  - name: stg_orders
    columns:
      - name: order_date
        tests:
          - not_null
```

```yaml
      - name: order_date
        description: Date (shop local time) that the order was placed
        tests:
          - not_null
```

The `not_null` test on `orders.order_date` would have failed this run with 43 rows.

To guard the time-zone handling too, add a dbt unit test pinned to the known case:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_shop_local
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 54, user_id: 54, ordered_at: '2018-02-26 11:11:56', status: completed, order_ts: '2018-02-26 11:11:56'}
          - {id: 73, user_id: 19, ordered_at: null, status: completed, order_ts: '2018-03-15 19:45:20'}
    expect:
      rows:
        - {order_id: 54, order_date: '2018-02-26'}
        - {order_id: 73, order_date: '2018-03-16'}
```

Optionally, add a singular test (`tests/daily_revenue_matches_orders.sql`) that compares order counts per date between `orders` and `daily_revenue`. It should return no rows. This catches the incremental model drifting away from its source.
