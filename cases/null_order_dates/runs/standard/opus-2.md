claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 31 s | USD 0.185 | 2026-10-14 | project ad0b16b | prompt 3b339a6e8808

## 1. Root cause

**The bad data is in the raw `raw_orders` table. 8 March orders were loaded with `ordered_at = NULL`.** The models are not at fault. `stg_orders.sql` does `cast(ordered_at as date)`, so those orders end up with `order_date = NULL`.

The numbers reconcile exactly:

| | orders | amount |
|---|---|---|
| `orders`, March | 27 | 473.00 |
| `orders`, `order_date` NULL | **8** | **149.00** |
| Sum | 35 | 622.00 = `daily_revenue` / shop admin |

Other evidence:

- The NULL-dated orders are March orders. Order ids follow date order: 57 is 03-01, 63 is 03-06, 78 is 03-23 and 84 is 03-26. The NULL rows visible in the sample, **69 and 70**, fall inside that range.
- **`customers`:** `first_order = min(order_date)` is NULL for any customer whose only orders are NULL-dated. That gives the "two customers with no first order date": 62 customers appear in `region_revenue`, but only 60 have a `first_order`.
- **Why `daily_revenue` still shows the right number:** it is incremental (`models/daily_revenue.sql`) and only rebuilds `order_date >= max(order_date) - 3 days`. Its March rows were built before the NULLs arrived and are never revisited. `NULL >= ...` is also never true, so the NULL rows are never picked up. It is correct only by accident: a `--full-refresh` would drop March to 473 as well.
- **Why nothing failed:** no test covers `order_date` or `ordered_at`. Also, the nightly build doesn't run seeds, so it doesn't run seed tests either. That is why 32 tests were found but only 28 ran.

## 2. Fix

Fix the data. Don't guess dates in SQL.

1. List the affected orders and have the ingestion job reload `ordered_at` from the shop backend for them:
   ```sql
   select id, user_id, status from raw_orders where ordered_at is null;
   ```
   Also find out why ingestion produced NULLs, for example a timestamp format or parse change on that batch. Otherwise the problem will come back.
2. Rebuild, and fully refresh the incremental model so it can't drift from `orders`:
   ```bash
   dbt build --full-refresh -s stg_orders+
   ```
3. Check that March in `orders` is 35 orders / 622.00, that the `order_date is null` group is gone, and that `count(first_order)` in `customers` is 62.

## 3. Tests that would have caught it

Add these in `models/staging/schema.yml`, on a model the nightly build actually runs:

```yaml
  - name: stg_orders
    columns:
      - name: order_date
        tests:
          - not_null
```

and in `models/schema.yml` under `orders`:

```yaml
      - name: order_date
        tests:
          - not_null
```

Add a reconciliation test so the incremental table can't silently diverge from its source. `tests/daily_revenue_matches_orders.sql`:

```sql
-- fails if daily_revenue and orders disagree on any date (including orders with no date)
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
),
d as (
    select order_date, orders, revenue from {{ ref('daily_revenue') }}
)
select *
from o
full outer join d using (order_date)
where o.order_date is null
   or d.order_date is null
   or o.orders != d.orders
   or abs(o.revenue - d.revenue) > 0.005
```

Either `not_null` test would have failed in last night's build. The reconciliation test would also have failed, showing both the 8 undated orders and the March mismatch.
