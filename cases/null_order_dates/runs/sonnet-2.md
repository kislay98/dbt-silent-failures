claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 25 s | USD 0.068 | 2026-10-14 | project bef6b69 | prompt aaf31b99f700

## 1. Root cause

**The raw data, not the SQL.** In `raw_orders` (the seed/ingestion load that `stg_orders.sql` reads), 8 March rows have `ordered_at = NULL`: order ids 66-73. The ingestion job dropped the timestamp for them.

- `stg_orders.sql` derives `cast(ordered_at as date) as order_date`, so those 8 orders get `order_date = NULL`.
- In `orders` they land in the `NULL` month bucket: 8 orders and 149.00. That matches the gap exactly: 473 + 149 = 622 and 27 + 8 = 35.
- In `customers`, `min(order_date)` ignores NULLs. Customers 19 and 36 have only that one order, so their `first_order` is NULL. Customers 3 and 79 have other orders, so they look fine.

**Why `daily_revenue` is still right, and why no test failed:**
- `daily_revenue` is incremental and only reprocesses `order_date >= max(order_date) - 3 days`. The max is in April, so March has not been rebuilt since the rows were loaded. It still holds the correct figures from an earlier run, when `ordered_at` was populated. That is why it agrees with the shop admin.
- The incremental filter also drops NULL dates (`NULL >= x` is not true), so `daily_revenue` never shows a NULL-date group, and `not_null_daily_revenue_order_date` passes.
- `order_date` and `ordered_at` have no `not_null` test in `orders` or `stg_orders`, so the NULLs passed silently.
- All 34 nodes were built and tested without error, so `dbt build` reported success.

I'm inferring this from the log and the samples, since I can't query the warehouse.

## 2. Fix

1. **Restore the data first.** Reload `ordered_at` for order ids 66-73 from the shop admin, and fix the ingestion job that dropped it. Do not invent dates in dbt, for example with `coalesce`.
2. **Then rebuild:** `dbt build --full-refresh -s orders customers daily_revenue`. `orders` and `customers` rebuild on any run, but `daily_revenue` needs `--full-refresh`, because its window will never reach March on its own.

**Do not full-refresh `daily_revenue` before step 1.** It currently holds the only correct March numbers, and a rebuild from the bad data would overwrite them with a NULL-date group and fail `not_null`.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add `not_null` on the timestamp so bad loads fail at the staging layer:

```yaml
  - name: stg_orders
    columns:
      - name: ordered_at
        tests:
          - not_null
```

In `models/schema.yml`, add `not_null` on `orders.order_date`:

```yaml
      - name: order_date
        tests:
          - not_null
```

Add a reconciliation test, `tests/assert_daily_revenue_matches_orders.sql`, so a stale incremental table can't drift from `orders`:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
)

select *
from o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date is not distinct from d.order_date
where o.order_date is distinct from d.order_date
   or o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

The `not_null` on `stg_orders.ordered_at` would have failed the build the night the NULLs arrived. The reconciliation test would have flagged the 8-order, 149.00 gap between `orders` and `daily_revenue`.
