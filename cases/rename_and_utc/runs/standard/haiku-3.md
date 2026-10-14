claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 48 s | USD 0.008 | 2026-10-15 | project ad0b16b | prompt 11fb6c29d933

## 1. Root cause

**The ingestion job moved the order timestamp from `raw_orders.ordered_at` to a new `raw_orders.order_ts` column, and `stg_orders` still reads only `ordered_at`.**

- **Raw data:** `raw_orders.ordered_at` is NULL for orders placed from March onward. The 43 NULL-date orders are exactly the 35 March and 8 April orders in `daily_revenue`. Sample rows 63, 96, 93, 77, 84, 72 and 65 show this: `ordered_at` is NULL and `order_ts` holds the March/April timestamp. The NULL bucket in the monthly report (43 orders, $761) plus Jan–Feb ($911) gives the $1,672 total.
- **`models/staging/stg_orders.sql`:** `cast(ordered_at as date) as order_date` yields NULL for those 43 rows.
- **`models/orders.sql`:** passes `order_date` through unchanged.
- **`models/schema.yml`:** `orders.order_date` has no tests, and neither does `stg_orders.order_date`, so the NULLs pass silently.
- **`models/daily_revenue.sql`:** the incremental filter `where order_date >= (select max(order_date) - interval 3 day ...)` drops NULL rows. The model therefore never inserts them, and its `not_null` and `unique` tests on `order_date` pass. The March and April rows were written by earlier runs, and `delete+insert` only replaces keys present in the new batch, so they remain. `daily_revenue` looks correct while `orders` is wrong. A full refresh would have produced a NULL `order_date` group and failed `not_null`, so the problem stays hidden until then.
- **Why the build is green:** no test covers `orders.order_date`, and the one on `daily_revenue` is masked by the incremental filter.

**Secondary issue to verify:** `schema.yml` describes `order_date` as "Date (UTC)", but ops reconciles against the shop's local time and it matched until this load. Where both columns are populated, `order_ts` equals `ordered_at` (ids 25, 46, 47), so they share a convention. Order 73 is not in the sample, so I could not confirm its value. Check it before relying on the fix (see below).

## 2. Fix

**`models/staging/stg_orders.sql`:** fall back to `order_ts` when `ordered_at` is NULL.

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ordered_at is NULL for orders loaded since March; order_ts carries the same value
        coalesce(ordered_at, order_ts) as ordered_at,
        cast(coalesce(ordered_at, order_ts) as date) as order_date,
        status

    from source

)
```

**Before running, check order 73 and the column agreement:**

```sql
select id, ordered_at, order_ts, cast(coalesce(ordered_at, order_ts) as date) as order_date
from raw_orders where id = 73;

select count(*) from raw_orders
where ordered_at is not null and order_ts is not null and ordered_at <> order_ts;  -- expect 0
```

If order 73 reads `2018-03-16 01:15:20`, the columns are in shop-local time and the fix is correct. Then correct the `order_date` description in `models/schema.yml` from "Date (UTC)" to the shop's local date. If it reads `2018-03-15 14:15:20`, the value is UTC and the cast needs a timezone conversion to shop time before taking the date.

**Rebuild:**

```bash
dbt build --select stg_orders+
dbt run --select daily_revenue --full-refresh
```

The full refresh is needed because the incremental model only rebuilds the last three days of `max(order_date)`, which would leave the March rows stale. `daily_revenue` is derived entirely from `orders`, so the rebuild is safe.

I have not run any of this. The fix is verified by reading the models and the sample rows only.

## 3. Test that would have caught it

Add `not_null` on the staging date, which fails the build before `orders` and `daily_revenue` are built:

```yaml
# models/staging/schema.yml, under stg_orders
      - name: order_date
        tests:
          - not_null
```

Also add it to `orders`, and guard the `coalesce` assumption with a singular test:

```yaml
# models/schema.yml, under orders.columns
      - name: order_date
        tests:
          - not_null
```

```sql
-- tests/assert_ordered_at_matches_order_ts.sql
select id
from {{ ref('raw_orders') }}
where ordered_at is not null
  and order_ts is not null
  and ordered_at <> order_ts
```

The `not_null` tests would have failed on the 43 March and April rows. The singular test fails if the two columns ever disagree, which would mean one of them has a different timezone convention.
