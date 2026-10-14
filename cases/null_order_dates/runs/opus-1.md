claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 37 s | USD 0.146 | 2026-10-14 | project f769fdf | prompt 8e741d6ac5d0

# March revenue: root cause, fix, and tests

## 1. Root cause

**The data is wrong, not the model SQL.** In the warehouse table `raw_orders`, the `ordered_at` timestamp is `NULL` for the 8 orders with ids **66–73**. Those orders were placed between about 2018‑03‑09 and 2018‑03‑16. The ingestion job (or its source extract) loaded them without a timestamp.

The numbers match exactly:

| | orders | amount |
|---|---|---|
| `orders`, March | 27 | 473.00 |
| `orders`, `order_date IS NULL` | 8 | 149.00 |
| Sum = shop admin / `daily_revenue` | 35 | 622.00 |

The bad values spread like this:

- **`models/staging/stg_orders.sql`**: `cast(ordered_at as date)` turns NULL into a NULL `order_date`. The orders still exist, but they have no date, so they fall out of March into the `NULL` bucket in `orders`.
- **`models/customers.sql`**: `min(order_date)` and `max(order_date)` ignore NULLs.
  - Customers 19 and 36 have only one order each (73 and 66), so their `first_order` is NULL while `number_of_orders = 1`.
  - Customers 3 and 79 also have affected orders (69 and 67). They have other orders with valid dates, so they look fine, but their `most_recent_order` is now wrong.
- **`models/daily_revenue.sql` hides the problem.** It is incremental and only rebuilds `max(order_date) - 3 days`. Its rows for 03‑10 to 03‑16 were written before the timestamps went NULL, and they are never rebuilt. That is why it still shows 622.00. Those rows add up to exactly 8 orders and 149.00. On top of that, the `where order_date >= ...` filter silently drops NULL dates, so a NULL row never appears there.

**Why every test passed:** nothing tests `order_date` or `ordered_at` for nulls, and nothing reconciles `daily_revenue` against `orders`.

I can't see the seed or source files, so I can't tell you *why* those 8 timestamps are NULL. The usual cause is a different timestamp format in those rows (for example `2018/03/09`, a timezone suffix, or day‑first dates) that the loader coerced to NULL instead of rejecting. Check the source rows for ids 66–73 in the ingestion input, e.g. `seeds/raw_orders.csv` if that is what the loader reads.

## 2. Fix

**Data fix (the real fix):**

1. Correct `ordered_at` for orders 66–73 at the source. The shop admin has the right values.
2. Make the loader fail on unparseable timestamps instead of writing NULL.
3. Reload `raw_orders`.
4. Rebuild, including a full refresh of the incremental model so no stale state is left behind:

```bash
dbt build --full-refresh
```

`orders` and `customers` are rebuilt as tables, so they correct themselves once the raw data is fixed.

**Code hardening:** stop `daily_revenue` from silently dropping undated orders. If NULL dates are present, they should show up and fail a test.

```sql
    {% if is_incremental() %}
    -- payments can land a day or two after the order, so the last three days are rebuilt on every run
    where order_date >= (select max(order_date) - interval 3 day from {{ this }})
       or order_date is null
    {% endif %}
```

## 3. Tests that would have caught it

Add these to `models/staging/schema.yml` under `stg_orders`. Either one would have failed on this run:

```yaml
      - name: ordered_at
        tests:
          - not_null
      - name: order_date
        tests:
          - not_null
```

Add this to `models/schema.yml` under `orders` → `order_date`:

```yaml
        tests:
          - not_null
```

Add a reconciliation test, so the incremental table can't drift from its source unnoticed. Create `tests/assert_daily_revenue_reconciles_to_orders.sql`:

```sql
-- every order date in orders must match daily_revenue on count and revenue
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_count,
    d.orders as daily_revenue_count,
    o.revenue as orders_revenue,
    d.revenue as daily_revenue_revenue
from o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date is not distinct from d.order_date
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

On this run, that test would have returned the 7 stale dates from 03‑10 to 03‑16 plus the NULL‑date group.

## Unrelated issues I noticed

- `raw_customer_regions` has an `updated_at` column but no `unique` test on `customer_id`. If the feed ever sends more than one row per customer, `region_revenue` will double‑count. Its seed tests also didn't run in this build: 29 tests were found but 27 ran.
- `models/schema.yml` documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`.
