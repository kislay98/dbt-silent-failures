claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 33 s | USD 0.081 | 2026-10-14 | project f769fdf+wk6ext | prompt c5676332c543

## 1. Root cause

**`raw_orders` (`seeds/raw_orders.csv`, loaded by the ingestion job) and `models/staging/stg_orders.sql`.**

- `raw_orders` now has two timestamp columns: `ordered_at` and `order_ts`. From order 57 (2018-03-01) onward, the ingestion job leaves `ordered_at` NULL and fills only `order_ts`. That is 43 rows, all of March and April.
- `stg_orders.sql` still derives `cast(ordered_at as date) as order_date`, so those 43 orders get a NULL `order_date`. `strftime` then groups them under NULL, and `customers.first_order` and `most_recent_order` go NULL for customers such as 9, 12 and 93.
- **Why every test passed:**
  - Nothing tests `order_date` for not-null.
  - `daily_revenue` has `not_null` and `unique` on `order_date`, but they pass here. The incremental filter `order_date >= max(order_date) - 3 days` drops NULL dates, so the NULL group is never inserted.
  - Rows from earlier runs are simply left in place, so March and April look right but are stale. They are 622 + 139 = 761, which matches the 43 NULL orders. The total also still reconciles at 1672.00, so no row-count or sum check would flag it.
  - A `--full-refresh` of `daily_revenue` would lose March and April.
- **Time zone:** order 73 is `2018-03-15 19:45:20` in `order_ts` but `2018-03-16 01:15:20` on the admin screen. That is a +5:30 offset, so the stored timestamps are UTC and the shop's local time is almost certainly `Asia/Kolkata`. `order_date` is documented as UTC, but ops reconciles against local time. Simply coalescing to `order_ts` would therefore put order 73 on 03-15 and still not match the admin screen.

## 2. Fix

`stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ingestion stopped filling ordered_at from 2018-03-01 and now fills order_ts; both are UTC
        coalesce(ordered_at, order_ts) as ordered_at,
        -- order_date is the shop's local date, to match the admin screen
        cast(
            (coalesce(ordered_at, order_ts) at time zone 'UTC') at time zone '{{ var("shop_timezone", "Asia/Kolkata") }}'
            as date
        ) as order_date,
        status

    from source

)
```

Then:
- Update the `order_date`, `first_order` and `most_recent_order` descriptions in `models/schema.yml` from "Date (UTC)" to "Date (shop local time)".
- Run `dbt build --full-refresh -s stg_orders+`. The full refresh is needed because `daily_revenue` only rebuilds the last 3 days, so its old rows keyed by UTC date would otherwise stay.
- Ask the ingestion owner whether `ordered_at` is being retired. If so, drop it from the source.

Two things I could not check without the data:
- **Time zone:** `Asia/Kolkata` is inferred from the +5:30 offset. Confirm it with ops.
- **Month boundaries:** moving to local dates can shift a few orders across a day or month boundary, including in Jan and Feb. The 1672.00 total will not change, but monthly splits might. Compare against the admin screen after the rebuild.
- **Overlap check:** rows 54–56 show `ordered_at = order_ts`, so the two columns look equivalent. This query would confirm it:

  ```sql
  select count(*) from raw_orders where ordered_at is not null and ordered_at <> order_ts
  ```

## 3. Tests that would have caught it

Add to `models/staging/schema.yml` and `models/schema.yml`:

```yaml
      # stg_orders
      - name: order_date
        tests:
          - not_null
      - name: ordered_at
        tests:
          - not_null
```
```yaml
      # orders
      - name: order_date
        tests:
          - not_null
```

A reconciliation test, `tests/assert_daily_revenue_matches_orders.sql`, which returns rows on mismatch and so fails on one:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
)
select *
from o
full join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or o.revenue is distinct from d.revenue
```

A unit test pinning the time-zone behavior (dbt ≥1.8), so the rule is explicit:

```yaml
unit_tests:
  - name: stg_orders_falls_back_to_order_ts_and_uses_local_date
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: null, order_ts: '2018-03-15 19:45:20', status: completed}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
```

**Unrelated, worth checking:** `region_revenue` joins `raw_customer_regions`, which has an `updated_at` column. If it holds history, customers with several rows would fan out and inflate revenue. The seed is documented as "one row per customer" but has no `unique` test on `customer_id`. The same applies to `payment_method` in `raw_payment_methods`, which `orders.fees` joins to.
