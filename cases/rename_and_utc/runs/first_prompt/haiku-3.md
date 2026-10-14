claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 46 s | USD 0.007 | 2026-10-14 | project f769fdf+wk6ext | prompt c5676332c543

## 1. Root cause

**The raw data changed, and `stg_orders.sql` still reads only the old column.**

- In `raw_orders`, `ordered_at` is filled for ids up to 56 and NULL from id 57 (2018-03-01) onward. `order_ts` is filled for every row. Where both are present (54–56) they agree, so `order_ts` looks like the replacement column. The 43 NULL-date orders are exactly the rows where `ordered_at` is NULL.
- `models/staging/stg_orders.sql` selects only `ordered_at`, so `order_date` is NULL for those 43 orders. The monthly report has a NULL bucket of 43 orders / 761.00 and no March or April. The `sum(amount) = 1672.00` check passed because the NULL bucket is included in the total.
- `models/daily_revenue.sql` is incremental and filters `where order_date >= max(order_date) - 3 days`. NULL dates never match that filter, so the new orders never reach the table. The March and April rows are leftovers from earlier runs and are never rebuilt. The `not_null` test on `daily_revenue.order_date` passes only because no NULL row is ever written.

**There is a second, independent bug: timezone.**

- Order 73 has `order_ts = 2018-03-15 19:45:20` and the admin screen shows `2018-03-16 01:15:20`. That is a +05:30 offset, so `raw_orders` is UTC.
- `stg_orders.sql` does `cast(ordered_at as date)`, which gives the UTC date. Any order placed between 18:30 and 24:00 UTC lands on the previous day compared with shop-local time. Fixing the NULLs alone will not make the daily reconciliation match.

**Why no test caught it:** no `schema.yml` has a `not_null` on `order_date` or `ordered_at`. The existing `not_null` tests cover amounts, fees and keys, and the uniqueness and accepted-values tests ignore NULLs. Nothing compares the output against the raw table.

## 2. Fix

**`models/staging/stg_orders.sql`**: take the timestamp from either column and convert to shop-local time before taking the date.

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        coalesce(ordered_at, order_ts) as ordered_at,
        cast(
            (coalesce(ordered_at, order_ts) at time zone 'UTC') at time zone 'Asia/Kolkata'
            as date
        ) as order_date,
        status

    from source

)
```

Notes on this change:
- `Asia/Kolkata` is inferred from the +05:30 offset. Confirm the shop's zone name before merging.
- I made `order_date` the local date because ops reconciles against local time. The `schema.yml` descriptions for `orders`, `customers.first_order` and `customers.most_recent_order` say "Date (UTC)" and need updating. If downstream consumers need UTC, add an `order_date_local` column instead.
- Confirm with the ingestion owner that `order_ts` is the permanent replacement and is UTC. Once `ordered_at` is retired, drop the `coalesce`.

**Rebuild, including the incremental model.** The incremental filter will not rewrite March and April, so `daily_revenue` needs a full refresh:

```
dbt build --select stg_orders+ --full-refresh
```

**Do not run `dbt seed`.** The project declares `raw_*` as seeds, but the build log shows no seed runs, so these tables appear to come from the ingestion job. Seeding would overwrite them with CSVs that may not have `order_ts`.

## 3. Tests that would have caught it

**Generic tests in `models/staging/schema.yml`:**

```yaml
  - name: stg_orders
    columns:
      - name: order_date
        tests:
          - not_null
```

Also add `not_null` on `order_date` in `models/schema.yml` for `orders`. This alone would have failed 43 rows.

**Singular test `tests/assert_order_date_is_local_date.sql`:** this checks both the NULLs and the timezone rule against the raw table.

```sql
-- Fails for any order whose stg_orders.order_date is not the shop-local date of its raw timestamp
select
    r.id
from {{ ref('raw_orders') }} as r
join {{ ref('stg_orders') }} as s
    on s.order_id = r.id
where s.order_date is distinct from cast(
    (coalesce(r.ordered_at, r.order_ts) at time zone 'UTC') at time zone 'Asia/Kolkata'
    as date
)
```

**Row-count reconciliation test `tests/assert_orders_complete.sql`:** this catches silently dropped rows, which the amount check missed.

```sql
-- Fails if any raw order is missing from stg_orders
select 1
where (select count(*) from {{ ref('raw_orders') }})
   <> (select count(*) from {{ ref('stg_orders') }})
```

With these in place, the NULL-date rows would fail the build on the first nightly run instead of reaching the report.
