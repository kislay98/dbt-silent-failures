claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 35 s | USD 0.102 | 2026-10-15 | project ad0b16b | prompt 15c0aae6d239

## 1. Root cause

**`models/staging/stg_orders.sql`** reads only `ordered_at`, but the ingestion job changed what it loads into `raw_orders`. For the 43 orders from March onward (the ones in the `NULL` bucket), `ordered_at` is now empty and the timestamp arrives in the new `order_ts` column. `cast(ordered_at as date)` therefore gives `NULL`, and `orders.order_date` is `NULL` for those rows.

- **Why the report shows only January and February:** the monthly report groups by `order_date`, and the `NULL` bucket holds exactly 43 orders and 761.00. Together with 496 + 415 for January and February, that is the 1672.00 total, which is why `sum(amount)` still matches.
- **Why `daily_revenue` looks fine:** it is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. `NULL >= x` is never true, so the null-date rows are silently dropped, and the old March and April rows stay as stale leftovers. March and April there are 35 + 8 = 43 orders and 761.00, which matches the null bucket.
- **Why every test passed:** nothing tests `order_date` for nulls on `stg_orders` or `orders`, and the `not_null` tests on `daily_revenue` never see a null.
- **Timezone trap:** `order_ts` is UTC, while `ordered_at` matches the admin screen's shop-local time. Order 73 is 19:45:20 in `order_ts` and 01:15:20 the next day on the admin screen, so the offset is +5:30. A plain `coalesce(ordered_at, order_ts)` would put order 73 on 03-15 and break ops' daily reconciliation. The `order_date` description in `schema.yml` ("UTC") is also wrong. It is shop-local.

## 2. Fix

In `stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ingestion now sends new orders in order_ts (UTC) with ordered_at empty;
        -- ordered_at is shop-local time, so shift UTC by +05:30 to match the admin screen
        coalesce(ordered_at, order_ts + interval 5 hour + interval 30 minute) as ordered_at,
        cast(coalesce(ordered_at, order_ts + interval 5 hour + interval 30 minute) as date) as order_date,
        status

    from source

)
```

Then:

1. Run `dbt run --full-refresh -s daily_revenue+ stg_orders+`. A normal incremental run won't repair the stale `daily_revenue` rows.
2. Update the `order_date` descriptions to "shop-local date".
3. Check that the +5:30 offset holds for a few more orders on the admin screen, since I derived it from order 73 alone. After the rebuild, the `NULL` row should be gone and March and April should appear in `orders`.
4. Ask the ingestion owners to confirm the `order_ts` semantics (UTC) and whether `ordered_at` will return.

## 3. Tests

In `models/staging/schema.yml` and `models/schema.yml`:

```yaml
      - name: order_date   # on both stg_orders and orders
        tests:
          - not_null
```

A unit test pins the timezone behaviour using order 73:

```yaml
unit_tests:
  - name: stg_orders_falls_back_to_order_ts_in_shop_time
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 1, ordered_at: null, order_ts: '2018-03-15 19:45:20', status: placed}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
```

A singular test, `tests/orders_vs_daily_revenue.sql`, catches drift between `orders` and `daily_revenue`:

```sql
select o.order_date, o.n, d.orders
from (select order_date, count(*) n from {{ ref('orders') }} group by 1) o
full join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.n is distinct from d.orders
```

I haven't run any of this; the project files aren't in my working directory.
