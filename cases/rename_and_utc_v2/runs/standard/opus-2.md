claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 49 s | USD 0.222 | 2026-10-15 | project ad0b16b | prompt 15c0aae6d239

# Root cause: the order timestamp moved to a new column in a different time zone

## 1. Root cause

**The data:** the ingestion job changed the `raw_orders` load.

- For orders from March onward, it stopped filling `ordered_at` and wrote the timestamp into a new column, `order_ts`. Rows before March have both columns, and they are identical (orders 25, 46 and 47 in the sample).
- `order_ts` is not in the shop's local time. Order 73 is 2018-03-16 01:15:20 on the admin screen but 2018-03-15 19:45:20 in `order_ts`, which is 5h30m earlier. That is enough to move the order to the previous day.

**The file:** `models/staging/stg_orders.sql` still derives the date only from the old column:

```sql
cast(ordered_at as date) as order_date,
```

So all 43 March and April orders get a NULL `order_date`. Nothing fails because:

- **No test covers it.** No test checks `order_date` in `stg_orders` or `orders`, and `amount` is still fully populated. That is why `sum(amount)` is still 1672.00.
- **`daily_revenue` looks fine only because it is stale.** It is incremental. This run's filter `order_date >= max(order_date) - 3 days` drops NULL dates, so it inserted nothing and deleted nothing. The March and April rows you see (35 orders, 761.00) come from earlier runs. They exactly match the NULL bucket in `orders`. A `--full-refresh` today would wipe them.
- **`customers` is also affected.** `first_order` and `most_recent_order` are NULL for customers whose orders are all from March onward (e.g. 9, 47, 89 and 21 in the sample).

The obvious patch, `coalesce(ordered_at, order_ts)`, is wrong. It would put order 73 on 2018-03-15, and ops' daily reconciliation against the admin screen would break for every order near midnight. A side note: the `schema.yml` description "Date (UTC)" is inaccurate. `ordered_at` was effectively shop-local time, which is why the reconciliation used to agree.

## 2. Fix

**Preferred: a data fix.** Ask the ingestion team to populate `ordered_at` in shop-local time again, including a backfill for March and April. If that happens, no model change is needed.

**If `order_ts` is now the contract: a code fix** in `models/staging/stg_orders.sql`. This converts `order_ts` to local time only where `ordered_at` is missing:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ingestion stopped filling ordered_at in March 2018; order_ts is not in shop-local time
        coalesce(ordered_at, order_ts + interval '{{ var("order_ts_local_offset", "5 hours 30 minutes") }}') as ordered_at,
        cast(coalesce(ordered_at, order_ts + interval '{{ var("order_ts_local_offset", "5 hours 30 minutes") }}') as date) as order_date,
        status

    from source

)
```

**Caveat on the offset:** 5h30m is derived from one order. Before relying on it, confirm with ingestion which zone `order_ts` is in. Also spot-check a few March and April orders against the admin screen, including some after 2018-04-01, when Australian daylight saving ended.

- If the offset is constant, the code above is correct.
- If it varies with DST, replace the fixed interval with a proper zone conversion. For example, `timezone('<shop tz>', timezone('<order_ts tz>', order_ts))` in DuckDB with the ICU extension.

**Then rebuild:** run `dbt build --full-refresh -s daily_revenue+` once. This regenerates the March and April rows from the corrected `orders` instead of relying on the leftover rows.

## 3. Tests that would have caught it

**Not-null on the date** (`models/staging/schema.yml` and `models/schema.yml`). This would have failed on 43 rows:

```yaml
      - name: order_date
        tests:
          - not_null
```

**Reconciliation of `daily_revenue` against `orders`** (`tests/daily_revenue_matches_orders.sql`). This catches the stale-incremental masking as well as any future date drift:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)
select *
from o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or o.revenue is distinct from d.revenue
```

**Unit test pinning the local-time conversion** (`models/staging/schema.yml`). This uses order 73 as the known answer, so a naive `coalesce(ordered_at, order_ts)` would fail it:

```yaml
unit_tests:
  - name: stg_orders_uses_shop_local_date
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 1, status: placed, ordered_at: null, order_ts: '2018-03-15 19:45:20'}
          - {id: 25, user_id: 51, status: completed, ordered_at: '2018-01-28 09:00:52', order_ts: '2018-01-28 09:00:52'}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
        - {order_id: 25, order_date: '2018-01-28'}
```
