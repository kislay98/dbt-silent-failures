claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 37 s | USD 0.006 | 2026-10-14 | project f769fdf+wk6ext | prompt c5676332c543

## 1. Root cause

**The staging model reads the wrong timestamp column.** `jaffle_shop/models/staging/stg_orders.sql` builds `ordered_at` and `order_date` from `raw_orders.ordered_at`. Somewhere around March 2018 the ingestion job stopped populating `ordered_at` and started writing to `order_ts`. In the sample, every row from id 57 onward has `ordered_at = NULL` and a value in `order_ts`. The 43 NULL `order_date` rows are those orders, and `stg_orders` passes the NULL through to `orders`, `customers`, and `daily_revenue`.

The data confirms this:
- `orders` has 43 NULL-date orders totalling 761.0. `daily_revenue` has March (35 orders, 622.0) plus April (8 orders, 139.0), which is exactly 43 orders and 761.0. The 1672.00 total is the same in both because the amounts are not lost, only the dates.
- `daily_revenue` still shows March and April because it is incremental. Its `where order_date >= max(order_date) - 3 days` filter never matches NULL dates, so the rows written before the source changed are never rebuilt or removed.
- `customers` shows NULL `first_order` and `most_recent_order` for customers 9, 12, and 93, whose only orders have NULL dates.

**Timezone problem.** `order_ts` is UTC. Order 73 is `2018-03-15 19:45:20` UTC, which is `2018-03-16 01:15:20` on the admin screen, an offset of +05:30. Ops reconciles by local date, but `order_date` is documented and computed as a UTC date (`models/schema.yml`, `orders.order_date` and `customers.first_order`/`most_recent_order`). Orders placed after 18:30 UTC on a month's last day will land in the wrong month even after the NULL fix. I inferred Asia/Kolkata from the offset. Please confirm the shop's zone.

**Why the tests passed:**
- No test covers `order_date` in `models/schema.yml` or `stg_orders` in `staging/schema.yml`, and `ordered_at` has no not_null test.
- `daily_revenue`'s `not_null` and `unique` tests only see rows the incremental window selects, and NULL never satisfies `>=`, so the NULL group never reaches the table.
- Nothing compares totals or row counts between `orders` and `daily_revenue`.

## 2. Fix

**a) `jaffle_shop/models/staging/stg_orders.sql`**: read `order_ts` and convert to shop-local date.

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        order_ts as ordered_at,
        cast(
            (order_ts at time zone 'UTC') at time zone '{{ var("shop_timezone", "Asia/Kolkata") }}'
            as date
        ) as order_date,
        status

    from source

)
```

Before merging, confirm `order_ts` is populated for all rows: `select count(*) from raw_orders where order_ts is null` should return 0. Also check that `ordered_at` equals `order_ts` wherever both are populated, which the sample suggests.

**b) Update the descriptions** in `models/schema.yml` for `orders.order_date`, `customers.first_order`, and `customers.most_recent_order`, from "Date (UTC)" to "Date (shop local time)". This also fixes the reference to `total_order_amount` in `customers`, which doesn't exist in `customers.sql`.

**c) Rebuild `daily_revenue` fully.** The incremental window will not rewrite the stale March and April rows, so:

```
dbt build --full-refresh -s stg_orders+
```

Running this without `--full-refresh` would leave the old March and April rows in place.

## 3. Tests that would have caught it

**a) Column-level tests** in `models/staging/schema.yml` and `models/schema.yml`:

```yaml
  - name: stg_orders
    columns:
      - name: order_date
        tests:
          - not_null
      - name: ordered_at
        tests:
          - not_null
```

```yaml
  - name: orders
    columns:
      - name: order_date
        tests:
          - not_null
```

Also declare `raw_orders` as a source with `not_null` on `order_ts` so the ingestion contract is checked before staging runs.

**b) A reconciliation test** in `tests/assert_daily_revenue_matches_orders.sql`. This catches any gap between `orders` and `daily_revenue`, including the NULL dates:

```sql
with daily as (
    select sum(orders) as n, sum(revenue) as amount from {{ ref('daily_revenue') }}
),

orders_total as (
    select count(*) as n, sum(amount) as amount from {{ ref('orders') }}
)

select *
from daily
cross join orders_total
where daily.n <> orders_total.n
   or abs(daily.amount - orders_total.amount) > 0.005
```

With the current data, this test fails: `daily_revenue` has 43 fewer orders than `orders`.
