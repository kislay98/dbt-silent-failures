claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 42 s | USD 0.008 | 2026-10-15 | project ad0b16b | prompt 15c0aae6d239

## 1. Root cause

**The data:** `raw_orders` has two timestamp columns. For newer rows the ingestion job fills `order_ts` and leaves `ordered_at` NULL. 43 rows are affected. The March and April rows in `daily_revenue` total exactly 43 orders (35 + 8), so these are the March and April orders. Order 73 is one of them.

**The model:** `models/staging/stg_orders.sql` derives everything from `ordered_at` only:

```sql
ordered_at,
cast(ordered_at as date) as order_date,
```

For those 43 rows `order_date` is NULL. `orders.sql` keeps them, so the monthly report puts them in a NULL bucket (761.00). January + February + NULL = 496 + 415 + 761 = 1672.00, which is why the total is unchanged.

**Why `daily_revenue` looks unchanged:** it is incremental with `delete+insert` keyed on `order_date`. The March and April dates no longer appear in the new query, so nothing deletes or replaces those rows. They stay at their old values. NULL dates are also dropped by `where order_date >= ...`.

**Why every test passed:** no test covers the missing values.
- `orders.order_date` has no tests in `models/schema.yml`.
- `stg_orders` has no test on `ordered_at`.
- The `unique` and `not_null` tests on `daily_revenue.order_date` only see rows that survived the incremental filter, so the NULL-date rows never reach them.

**Timezone:** the admin screen shows shop local time. Order 73 has `order_ts` 2018-03-15 19:45:20 and the admin shows 2018-03-16 01:15:20, a +05:30 offset. The model documents `order_date` as UTC. So filling the NULLs from `order_ts` with a plain cast would still disagree with the admin screen for any order after 18:30 UTC.

One thing I could not settle from this output: in the sample, every row with `ordered_at` populated has `ordered_at` equal to `order_ts`. Before fixing the timezone, check one January or February order with `order_ts` after 18:30 against the admin screen. That tells you which clock the existing `ordered_at` values use.

## 2. Fix

**`models/staging/stg_orders.sql`** (minimal fix, which restores the 43 rows):

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- newer loads leave ordered_at empty; order_ts holds the same instant
        coalesce(ordered_at, order_ts) as ordered_at,
        cast(coalesce(ordered_at, order_ts) as date) as order_date,
        status

    from source

)
```

If the check above confirms that `order_ts` is UTC and the shop reports local time, the date must be computed in the shop's zone. The +05:30 offset in the example suggests `Asia/Kolkata`, but confirm the actual zone with ops before using it:

```sql
cast((order_ts at time zone 'UTC') at time zone '<shop_tz>' as date) as order_date,
```

Apply this only to rows where `ordered_at` is NULL, or to all rows, depending on what the check shows. Applying it to rows where `ordered_at` is already local would shift those dates by a day.

**Data repair:** `orders` is a table, so it rebuilds on every run. `daily_revenue` is incremental and needs a full refresh to drop the stale March and April rows:

```
dbt build --select stg_orders+ --full-refresh
```

## 3. Tests that would have caught this

**`models/staging/schema.yml`**, under `stg_orders`:

```yaml
      - name: ordered_at
        tests:
          - not_null
```

This fails on the 43 rows before the fix and would have blocked the nightly build.

**`models/schema.yml`**, under `orders`:

```yaml
      - name: order_date
        tests:
          - not_null
```

**`tests/assert_orders_cover_raw_orders.sql`**, a singular test that returns any raw order missing from `orders`:

```sql
select r.id
from {{ ref('raw_orders') }} r
left join {{ ref('orders') }} o
    on r.id = o.order_id
where o.order_id is null
```

The `not_null` tests check values, and this check catches a row going missing entirely. Together they would have failed on the first run that loaded the new format.
