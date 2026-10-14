claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 25 s | USD 0.092 | 2026-10-14 | project ad0b16b | prompt 3b339a6e8808

## 1. Root cause

**The data:** `raw_orders.ordered_at` is NULL for 8 orders (e.g. order 69 for customer 3, and order 70 for customer 8). The ingestion job loaded them without a timestamp. These 8 orders total 149.00, which is exactly the gap: 622 − 473 = 149 and 35 − 27 = 8. Their `order_date` is NULL, so they are the NULL month row in `orders` (8 orders, 149.0). Every other month agrees.

**Why the 8 orders show up in different places:**
- `models/staging/stg_orders.sql` computes `cast(ordered_at as date) as order_date` and passes the NULLs through. `orders` is a full-refresh table, so it is current and those 8 orders fall outside every month. March is short.
- `models/daily_revenue.sql` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`, which is roughly the last three days of April. The March rows are therefore stale from an earlier run, when those orders still had dates. That is why the dashboard and shop admin still say 622 / 35.
- A NULL `order_date` can never satisfy the incremental filter, so these orders can never re-enter `daily_revenue`. The model also hides the problem. A full refresh would create a NULL `order_date` group and the `not_null` test would fail, but the incremental run skips that group, so the test passes.
- `models/customers.sql` uses `min(order_date)`, which ignores NULLs. A customer whose only orders are undated gets `first_order` NULL, even though `number_of_orders` is still above 0. That explains the two March customers with no first order date.

No test checks `ordered_at` or `order_date`. `orders.order_date` and `stg_orders.ordered_at` have no `not_null`, so the build passes.

## 2. Fix

1. **Data (the real fix):** reload the 8 orders' `ordered_at` from the shop admin source in the ingestion job. The warehouse can't recover the dates, since `daily_revenue` only holds aggregates. To list the affected rows:
   ```sql
   select id, user_id, status from raw_orders where ordered_at is null;
   ```
2. **Rebuild the stale model:** after the reload, run `dbt build --full-refresh -s daily_revenue+ orders+ customers`. March should then show 622.00 / 35 in `orders`, `daily_revenue` and `customers`. Without the full refresh, `daily_revenue` would not pick up corrections to old dates.
3. **Optional hardening:** `daily_revenue` is cheap to compute. Making it a plain `table` removes this class of staleness. If you keep it incremental, run a periodic full refresh and widen the lookback window.

## 3. Tests that would have caught it

Add `not_null` to `models/staging/schema.yml` under `stg_orders`, and to `orders.order_date` in `models/schema.yml`:

```yaml
      - name: ordered_at
        tests:
          - not_null
```

This would have failed the nightly build on the 8 rows. Add a reconciliation test so drift in the incremental model is caught too, as a singular test at `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with o as (
    select date_trunc('month', order_date) as month, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
),
d as (
    select date_trunc('month', order_date) as month, sum(orders) as orders, sum(revenue) as revenue
    from {{ ref('daily_revenue') }}
    group by 1
)
select *
from o
full outer join d using (month)
where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

This returns rows for March today (27 vs 35 orders, 473 vs 622 revenue), and for the NULL month, so it fails until the data is fixed.
