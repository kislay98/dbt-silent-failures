claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 34 s | USD 0.079 | 2026-10-14 | project bef6b69 | prompt aaf31b99f700

## 1. Root cause

**`raw_orders` (the ingestion-loaded table, which `models/staging/stg_orders.sql` reads) has `ordered_at = NULL` for 8 orders, ids 66-73.** These are March orders, and `stg_orders.order_date` is `cast(ordered_at as date)`, so they get a NULL `order_date`. The evidence:

- The `orders` month rollup has a `NULL` bucket of 8 orders and 149.00. That is exactly the gap: 473 + 149 = 622 and 27 + 8 = 35.
- `daily_revenue` for 3/10 to 3/16 holds 1+3+2+1+1 = 8 orders totalling 28+58+31+29+3 = 149.00. Those rows were computed earlier, when `ordered_at` was still populated.
- `customers` 19 and 36 have a single order each (73 and 66), both with NULL dates. `min(order_date)` ignores NULLs, so their `first_order` is NULL. Customer 79 has another dated order, so it is unaffected.
- The `ordered_at` values were lost in the load. The source system still has them, since the shop admin shows 622.

No test caught it, and `daily_revenue` hid it, for these reasons:

- Nothing tests `ordered_at` or `order_date` for nulls. `orders.order_date` has no tests, and `stg_orders` only tests `order_id` and `status`.
- `daily_revenue` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. March rows are never recomputed, so the dashboard kept the old correct numbers. That filter also drops NULL dates (`NULL >= x` is not true), so the `not_null` and `unique` tests on `daily_revenue.order_date` pass.
- As a result, `orders` and `daily_revenue` disagree silently, and `dbt build` stays green.

I haven't seen the seed or raw data itself. The 8-order and 149.00 match is the strongest evidence, but check the raw rows directly.

## 2. Fix

1. **Restore `ordered_at` for ids 66-73 in `raw_orders`.** Re-extract from the shop admin and reload, then find out why the ingestion job produced NULLs, for example a timestamp format change or a failed parse.
2. **Back up `daily_revenue` before touching it**, for example `create table daily_revenue_bak as select * from daily_revenue`. It currently holds the only correct copy of the March figures.
3. Run `dbt build`, then `dbt run -s daily_revenue --full-refresh` so the incremental table is rebuilt from the corrected data. Don't full-refresh before step 1. It would write a NULL-date row, fail `not_null`, and lose the 622.
4. Check that March shows 35 orders and 622.00, the NULL bucket is gone, and customers 19 and 36 have a `first_order`.

If the source can't be re-pulled quickly, a stopgap is to patch the dates by hand. The ids are monotonic with dates (65 is on 3/8, 74 on 3/17), so the likely mapping is 66→3/10, 67-69→3/11, 70-71→3/12, 72→3/14, 73→3/16. That is an inference, so confirm it against per-order amounts in the shop admin before using it.

## 3. Tests that would have caught it

Add to `models/staging/schema.yml`:

```yaml
  - name: stg_orders
    columns:
      - name: ordered_at
        tests:
          - not_null
```

Also add `not_null` on `orders.order_date` in `models/schema.yml`. Then add a reconciliation test, `tests/assert_daily_revenue_matches_orders.sql`:

```sql
select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_count, d.orders as daily_count,
    o.revenue as orders_revenue, d.revenue as daily_revenue
from (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
) o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where o.order_date is distinct from d.order_date
   or o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

This fails today on the NULL-date row and on the March dates missing from `orders`. It will also catch any future drift between the incremental table and its source.
