claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 37 s | USD 0.147 | 2026-10-14 | project f769fdf | prompt 8e741d6ac5d0

# March revenue: 8 orders lost their `ordered_at` in `raw_orders`

## 1. Root cause

**The bug is in the data, not the model code.** In the warehouse table `raw_orders`, which the ingestion job loads, rows **`id` 66–73 have `ordered_at = NULL`**. `stg_orders.sql` passes that through as `cast(ordered_at as date) as order_date`, so those 8 orders have a NULL `order_date` in every downstream model.

The numbers match exactly:

| | orders | amount |
|---|---|---|
| `orders`, March | 27 | 473.00 |
| `orders`, `NULL` month | 8 | 149.00 |
| **Total** | **35** | **622.00** (matches dashboard and shop admin) |

**Why `daily_revenue` is still right.** It is incremental (`delete+insert`) and only rebuilds `order_date >= max(order_date) - 3 days`. Its rows for 2018-03-10 through 03-16 were built before the timestamps were lost, and they have never been reprocessed. Those dates hold 1+3+2+1+1 = 8 orders, which are orders 66–73. So the dashboard is showing correct but stale rows. A `--full-refresh` would produce the same wrong numbers as `orders`, plus a NULL `order_date` row.

**Effect on `customers`.**
- Customers 19 and 36 have no order other than 73 and 66. Their `first_order` and `most_recent_order` are NULL.
- The effect is wider than finance noticed. Customers 3 (order 69) and 79 (order 67) also ordered in March. Their `most_recent_order` (2018-01-27 and 2018-02-28) is silently wrong. The same goes for 53, 8, 42 and 30 (orders 68, 70, 71, 72).

**Why every test passed.** Nothing tests `ordered_at` or `order_date` for NULLs. Also note that seeds are excluded from the build: 29 tests are found but only 27 run. So a test placed on the raw seed table wouldn't run either.

## 2. Fix

1. **Re-load `raw_orders` ids 66–73 from the source system (the shop admin)** with their real `ordered_at` timestamps. Don't hand-type the values. `daily_revenue` suggests the dates were 66 → 03-10, 67–69 → 03-11, 70–71 → 03-12, 72 → 03-14, 73 → 03-16, but the times of day are unknown.
2. **Find out why the ingestion job wrote NULLs.** A typical cause is a changed timestamp format that the loader handles with a `try_cast`/lenient parse. Make it fail loudly instead.
3. **Rebuild.** `orders`, `customers` and `region_revenue` are full tables and fix themselves on the next run. Run `daily_revenue` with a full refresh so it is consistent with `orders` rather than accidentally right:
   ```
   dbt build --full-refresh -s daily_revenue+
   ```
   Or run `dbt build --full-refresh` for everything.

## 3. Tests that would have caught it

Put these on the staging and mart models, since seed tests are excluded from your build. In `models/staging/schema.yml`, under `stg_orders`:

```yaml
      - name: ordered_at
        tests:
          - not_null
```

In `models/schema.yml`, under `orders`:

```yaml
      - name: order_date
        description: Date (UTC) that the order was placed, from the ordered_at timestamp
        tests:
          - not_null
```

Also add a reconciliation test. It catches the incremental model drifting from its source for any reason, which here is what hid the problem:

```sql
-- tests/assert_daily_revenue_matches_orders.sql
with from_orders as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

),

from_daily as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select *
from from_orders
full outer join from_daily using (order_date)
where from_orders.orders is distinct from from_daily.orders
   or abs(coalesce(from_orders.revenue, 0) - coalesce(from_daily.revenue, 0)) > 0.005
```

On the current data this test returns rows for the NULL date and for 03-10, 03-11, 03-12, 03-14 and 03-16, so the build would have failed.
