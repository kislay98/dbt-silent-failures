claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 22 s | USD 0.087 | 2026-10-14 | project ad0b16b | prompt 3b339a6e8808

## 1. Root cause

**`raw_orders` (loaded by the ingestion job) has 8 orders with `ordered_at = NULL`.** These are the missing March orders.

- `stg_orders.sql` does `cast(ordered_at as date) as order_date`, so these 8 orders get `order_date = NULL`.
- `orders` keeps them (it's a left join from orders), but they land in the `NULL` month bucket: 8 orders, 149.00.
- The gap is exact: 473 + 149 = 622 and 27 + 8 = 35. So all 8 were March orders in the earlier, correct load that the dashboard and shop admin still reflect.
- `customers` has `min(order_date)` and `max(order_date)` of NULL for any customer whose only orders are undated. That explains the two March customers with no first order date. They still count in `number_of_orders`, which is why `orders` sums to 99 against 60 non-null `first_order`.

**Why `daily_revenue` still shows 622.**
- Its incremental filter is `order_date >= (select max(order_date) - interval 3 day from {{ this }})`. That excludes NULL dates (NULL comparisons are false) and only touches the last 3 days of April.
- March rows were built on an earlier run, when `ordered_at` was still populated, and are never recomputed. The dashboard is stale, not right by design.
- A full refresh would produce a NULL `order_date` group and fail the `not_null` test. I inferred this from the code and numbers; I didn't run it.

**Why every test passed.** Nothing tests `order_date` or `ordered_at` for nulls. `orders.order_date` has no tests at all. `orders.status` and `amount` are populated for these rows, so the existing tests are satisfied.

## 2. Fix

1. **Fix the source.** I can't recover the dates from the warehouse data. Ask the ingestion owners why `ordered_at` went NULL for these 8 orders, then re-load them. To list them:
   ```sql
   select id, user_id, status from raw_orders where ordered_at is null;
   ```
2. **Rebuild downstream.** `daily_revenue` won't self-heal, so run a full refresh:
   ```
   dbt build --full-refresh -s stg_orders+ 
   ```
   Alternatively, widen the incremental window or add a periodic full refresh.
3. **Make the incremental model fail loudly instead of dropping rows.** Don't coalesce NULL dates into a fake date.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, under `stg_orders`:
```yaml
      - name: ordered_at
        tests:
          - not_null
```
In `models/schema.yml`, under `orders.order_date`:
```yaml
        tests:
          - not_null
```
A reconciliation test so the stale incremental table is caught too, at `tests/assert_daily_revenue_matches_orders.sql`:
```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
)
select coalesce(o.order_date, d.order_date) as order_date
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.order_date is null
   or d.order_date is null
   or o.orders != d.orders
   or abs(o.revenue - d.revenue) > 0.005
```
The `not_null` tests would fail at the staging layer on the first bad load. The reconciliation test would flag the March mismatch even if the dates were silently wrong.
