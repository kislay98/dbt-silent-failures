claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 35 s | USD 0.192 | 2026-10-14 | project ad0b16b | prompt 3b339a6e8808

## 1. Root cause

The problem is in the **data, not the SQL**. In `raw_orders` (loaded by the ingestion job), **8 March orders have `ordered_at = NULL`**. Orders 69 and 70 appear in the sample, and their ids fall between order 63 (2018-03-06) and order 78 (2018-03-23).

The numbers line up exactly:

| | orders | amount |
|---|---|---|
| `orders`, March | 27 | 473.00 |
| `orders`, `order_date IS NULL` | **8** | **149.00** |
| sum | 35 | 622.00 = `daily_revenue` / shop admin |

Here is how that one data defect causes each symptom:

- **`models/staging/stg_orders.sql`**: `cast(ordered_at as date)` carries the NULL through. The orders keep their payments, so `amount` is still correct, but they no longer belong to any month. Totals by status, region and customer lifetime value all still sum to 1672, so nothing looked off there.
- **`models/customers.sql`**: `min(order_date)` returns NULL for the two customers whose only orders are among the 8. That is why they show no `first_order` even though they have orders and payments.
- **`models/daily_revenue.sql`** is incremental. Its March rows were built on earlier runs, when `ordered_at` was still populated. Each run now only rebuilds rows where `order_date >= max - 3 days` (April). That predicate also excludes NULLs, so the stale but correct March rows survive. As a result, `daily_revenue` agrees with the shop admin, and `orders` is the table that is wrong.
- **Why every test passed:** nothing tests `ordered_at` or `order_date` for nulls. The incremental filter keeps the NULL group out of `daily_revenue`, so its `not_null_daily_revenue_order_date` test never sees it. A `--full-refresh` would have produced a NULL row there and failed that test.

## 2. Fix

Fix the source data, then rebuild:

1. List the broken rows and get their real timestamps from the shop admin or source system:
   ```sql
   select id, user_id, status from raw_orders where ordered_at is null order by id;
   ```
   Then fix the ingestion job so `ordered_at` is no longer lost. A typical cause is a timestamp format change that the loader silently coerces to NULL. Re-load `raw_orders`, or patch the 8 rows. Comparing `daily_revenue` with `orders` day by day for March shows which dates are missing, which helps cross-check the patch.
2. Rebuild everything, including a full refresh of the incremental model so it can't hide drift:
   ```bash
   dbt build --full-refresh -s stg_orders+
   ```
   Expected result: `orders` March = 35 orders / 622.00, no NULL month, and every customer with orders has a `first_order`.

## 3. Tests that would have caught it

Add not_null tests on the date column. They fail on the current data (8 rows):

```yaml
# models/staging/schema.yml, under stg_orders
      - name: ordered_at
        tests:
          - not_null

# models/schema.yml, under orders
      - name: order_date
        tests:
          - not_null
```

Also add a reconciliation test, so `daily_revenue` can't silently drift from `orders` again:

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
   or round(from_orders.revenue, 2) is distinct from round(from_daily.revenue, 2)
```

On the current data, this returns the NULL-date group from `orders` plus the March days where the counts differ.
