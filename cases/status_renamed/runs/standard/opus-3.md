claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 20 s | USD 0.171 | 2026-10-15 | project ad0b16b | prompt c1e2f11386cd

## 1. Root cause

**The data feed changed: `raw_orders` now carries the status in a new `order_status` column, and the legacy `status` column is NULL for 43 orders.** `jaffle_shop/models/staging/stg_orders.sql` still selects only `status`, so those 43 orders reach `orders` and `customers` with `status = NULL`.

The evidence in your outputs:

- `information_schema.columns` shows `raw_orders` has both `status` and `order_status`.
- In the `raw_orders` sample, older rows have both columns filled with the same value (`completed` / `completed`). Newer rows, from about March 2018 on, have `status = NULL` but `order_status` filled (`placed`, `shipped`, …).
- `orders` by status shows `NULL` for 43 orders worth 761.00. `amount` is unaffected, so `sum(orders.amount)` is still 1672.00.
- In `customers.sql`, `customer_payments` computes `case when orders.status = 'completed' then amount else 0 end`. Any order whose status is NULL counts as 0. Completed revenue therefore drops from 1103.00 to 824.00. The missing 279.00 is the completed orders that now have a NULL status.

**Why every test passed:**

- Neither `stg_orders.status` nor `orders.status` has a `not_null` test.
- dbt's `accepted_values` test only flags values `not in (...)`. `NULL not in (...)` evaluates to NULL, not true, so NULL rows are never flagged.

## 2. Fix

In `jaffle_shop/models/staging/stg_orders.sql`, read the new column and fall back to the legacy one:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(order_status, status) as status

    from source

)
```

I'd make `order_status` the primary source because it appears to be populated on every row, while `status` is the one going NULL. Check both assumptions before you ship:

```sql
-- should be 0: no row where both are null
select count(*) from raw_orders where status is null and order_status is null;
-- should be 0: the columns never disagree when both are set
select count(*) from raw_orders where status <> order_status;
```

After `dbt build`, `select round(sum(completed_lifetime_value), 2) from customers` should return 1103.00. It should also equal `select sum(amount) from orders where status = 'completed'`.

## 3. Tests that would have caught it

In `jaffle_shop/models/staging/schema.yml`, and the same on `orders.status` in `models/schema.yml`:

```yaml
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

The `not_null` test alone would have failed this build with 43 rows.

For a guard against future drift, add a reconciliation test, `tests/assert_completed_revenue_reconciles.sql`. It fails if `customers` and `orders` disagree on completed revenue or if any order has no status:

```sql
select *
from (
    select
        (select round(sum(completed_lifetime_value), 2) from {{ ref('customers') }}) as customers_completed,
        (select round(sum(amount), 2) from {{ ref('orders') }} where status = 'completed') as orders_completed,
        (select count(*) from {{ ref('orders') }} where status is null) as null_status_orders
)
where customers_completed <> orders_completed
   or null_status_orders > 0
```

Two smaller things I noticed:

- `models/schema.yml` documents a `customers.total_order_amount` column, but the model outputs `customer_lifetime_value`. That doc entry is stale.
- `daily_revenue` is incremental, so if you also change status-dependent logic there later, run it once with `--full-refresh`. Its current output doesn't depend on status, so this fix doesn't need it.
