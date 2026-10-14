claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 38 s | USD 0.200 | 2026-10-14 | project ad0b16b | prompt 3b339a6e8808

## 1. Root cause

The problem is in the data, not the SQL. **8 rows in `raw_orders` have `ordered_at = NULL`**, for example order ids 69 and 70 in the samples. All 8 are March orders.

The numbers reconcile exactly:

| | orders | amount |
|---|---|---|
| `orders`, March | 27 | 473.00 |
| `orders`, `order_date IS NULL` | 8 | 149.00 |
| **Sum** | **35** | **622.00** = `daily_revenue` / shop admin |

How the NULLs spread:

- **`models/staging/stg_orders.sql`** casts `ordered_at` to `order_date`, so NULL stays NULL.
- **`models/orders.sql`** keeps those 8 orders with all their payments, so `amount`, the payment columns and `fees` look fine. They fall into the `NULL` month bucket instead of March.
- **`models/customers.sql`**: `min(order_date)` ignores NULLs. Two customers whose only orders are among these 8 get `first_order = NULL`, even though `number_of_orders` is at least 1.
- **`models/daily_revenue.sql`** is incremental. It only reprocesses `order_date >= max(order_date) - 3 days`, so the March rows it built before the timestamps went NULL were never touched again. NULL dates never pass the `where` filter. That is why it still agrees with the shop admin, and why `not_null_daily_revenue_order_date` passes. A `--full-refresh` would produce a NULL-date group and fail that test.

The raw table is a dbt seed (`ref('raw_orders')`, so `seeds/raw_orders.csv`) loaded by your ingestion job. The table type is `TIMESTAMP` and the values are NULL, so the timestamps were lost on load or upstream. A common cause is the March rows arriving in a different format and being NULLed by a `try_cast` or a lenient CSV parse. I can't see the CSV or the ingestion code, so check the source file for those 8 ids.

**Why no test caught it:**
- Nothing tests `ordered_at` or `order_date` for nulls, at any layer.
- The nightly build excludes seeds. The log shows 35 nodes = 7 models + 28 tests; the 5 seeds and the 4 seed tests (32 − 28) never ran.

Confirm the affected rows:
```sql
select id, user_id, status from raw_orders where ordered_at is null order by id;
select customer_id, number_of_orders from customers where first_order is null and number_of_orders > 0;
```

## 2. Fix

**Data fix (the real one):** restore `ordered_at` for the 8 orders in `raw_orders` from the source system or shop admin. Fix the ingestion parsing that dropped them, then reload. Then rebuild:

```bash
dbt build --select stg_orders+ --full-refresh
```

`orders` and `customers` are tables and correct themselves. The `--full-refresh` on `daily_revenue` checks that it rebuilds from scratch to the same 622.00 / 35. Don't patch the dates in SQL with guesses; the shop admin is the source of truth.

**Hardening `daily_revenue`**, so a full rebuild can't quietly drop or group NULL dates. In `models/daily_revenue.sql`, inside `final`:

```sql
    from orders

    where order_date is not null
    {% if is_incremental() %}
    -- payments can land a day or two after the order, so the last three days are rebuilt on every run
    and order_date >= (select max(order_date) - interval 3 day from {{ this }})
    {% endif %}
```

Only add this together with the tests below. Otherwise it would hide the problem the same way the incremental filter did.

## 3. Tests that would have caught this

**Not-null on the order timestamp**, in `models/staging/schema.yml` under `stg_orders`:
```yaml
      - name: ordered_at
        tests:
          - not_null
      - name: order_date
        tests:
          - not_null
```
Add the same to `order_date` under `orders` in `models/schema.yml`. Either test fails with 8 rows.

**Reconciliation** between the incremental model and its source, in `tests/assert_daily_revenue_matches_orders.sql`:
```sql
-- daily_revenue is incremental and can drift from orders; totals must agree
with o as (
    select count(*) as orders, sum(amount) as revenue from {{ ref('orders') }}
),
d as (
    select sum(orders) as orders, sum(revenue) as revenue from {{ ref('daily_revenue') }}
)
select *
from o cross join d
where o.orders != d.orders or round(o.revenue, 2) != round(d.revenue, 2)
```
This fails today: 99 orders / 1672.00 against 91 / 1523.00.

Also stop excluding the seeds' tests from the nightly build, e.g. `dbt test --select resource_type:seed` after ingestion, so data-level checks on the raw tables actually run.
