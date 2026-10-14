claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 23 s | USD 0.090 | 2026-10-14 | project ad0b16b | prompt 3b339a6e8808

## 1. Root cause

**`raw_orders.ordered_at` is NULL for 8 orders, and `daily_revenue.sql` hid it.**

- `stg_orders.sql` computes `cast(ordered_at as date) as order_date`, so those 8 orders (e.g. 69 and 70) get `order_date = NULL`. In `orders` they land in the `NULL` month bucket: 8 orders, 149.00.
- The gaps in the question match that bucket exactly: 622 − 473 = 149.00 and 35 − 27 = 8 orders. So all 8 orders were in March, and `orders` has nothing else wrong.
- `daily_revenue.sql` (incremental, delete+insert) is why the dashboard still shows 622. An earlier run built it when those orders still had timestamps, so the March rows already included them.
  - The ingestion job later reloaded `raw_orders` with `ordered_at` empty for these rows.
  - Each incremental run only rebuilds `order_date >= max(order_date) - 3 days`. NULL dates never match that filter, and March is outside the window, so the stale March rows stay frozen. The NULL-dated orders never show up as their own row.
- `customers.sql` shows the same problem. `first_order = min(order_date)` ignores NULLs, so customers whose only orders are in that set (probably the two you noticed) get no `first_order`, even though `number_of_orders` counts them.
- No test caught it. `orders.order_date` and `stg_orders.ordered_at` have no `not_null` test, and no test reconciles `daily_revenue` against `orders`.

I only have samples, so I can't name all 8 order IDs. This query lists them:

```sql
select id, user_id, status from raw_orders where ordered_at is null;
```

## 2. Fix

1. **Fix the data upstream.** Have the ingestion job reload `ordered_at` for those 8 orders from the shop admin source. Don't coalesce to a made-up date in dbt.
2. **Rebuild the incremental model.** The 3-day window will never repair March, so run:
   ```
   dbt build --full-refresh -s daily_revenue+ customers
   ```
   After the reload, `orders`, `daily_revenue` and `customers` should all show 622.00 / 35 for March, and the two customers should get their `first_order`.
3. **Stop incremental from silently dropping NULLs.** After a full refresh a NULL-dated group would appear in `daily_revenue`, and the existing `not_null` test on `order_date` would then fail loudly. Make sure that is the behavior you want, rather than filtering it out.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add under `stg_orders`:

```yaml
      - name: ordered_at
        tests:
          - not_null
```

In `models/schema.yml`, add under `orders.order_date`:

```yaml
        tests:
          - not_null
```

Add a reconciliation singular test, `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must equal orders per day; also catches stale incremental rows
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as dr_orders,
       o.revenue, d.revenue as dr_revenue
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.order_date is distinct from d.order_date
   or o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

With the `not_null` tests, the nightly build would have failed the day the NULLs arrived. The reconciliation test fails for March today, because it compares `orders` (473.00 / 27) with `daily_revenue` (622.00 / 35).
