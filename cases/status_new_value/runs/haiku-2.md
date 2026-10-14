claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 33 s | USD 0.005 | 2026-10-14 | project bef6b69

## 1. Root cause

The models aren't miscounting completed orders. They count only `status = 'completed'`, while the admin also counts `status = 'delivered'`. The gap is exactly the delivered revenue.

- **March:** admin 279.00 − completed orders 143.00 = **136.00**. The March `delivered` row is 136.00.
- **Since January:** admin 1103.00 − `customers.completed_lifetime_value` sum 967.00 = **136.00**. The sample only shows `delivered` in March, so the whole gap is that one month's delivered revenue.
- `customers.completed_lifetime_value` is internally consistent: January 424 + February 400 + March 143 = 967.

The `delivered` status passes every test because it is a valid value. `jaffle_shop/models/staging/schema.yml` and `jaffle_shop/models/schema.yml` both list it in `accepted_values`. Nothing in the project says whether `delivered` counts as completed revenue, so the status list was widened without a revenue decision.

Files involved:
- `jaffle_shop/models/customers.sql`: `completed_amount` uses `orders.status = 'completed'` only.
- `jaffle_shop/models/schema.yml`: the `customers.completed_lifetime_value` description says "status completed", which disagrees with the admin. The `customers.total_order_amount` doc refers to a column that doesn't exist, which is a separate docs bug.

Not the cause: `daily_revenue` and `region_revenue` use total revenue, which already matches. The raw amounts are converted correctly, since DuckDB's `/` returns a double.

Caveat: I can't see the `doc("orders_status")` block or the admin's query. The "admin counts delivered" conclusion comes from the arithmetic, so confirm the intended definition with finance before changing anything.

## 2. Fix

If finance is right that delivered orders are completed revenue, change the filter in `jaffle_shop/models/customers.sql`:

```sql
sum(case when orders.status in ('completed', 'delivered') then amount else 0 end) as completed_amount
```

Also update the `completed_lifetime_value` description in `jaffle_shop/models/schema.yml` to say "completed or delivered", and fix the `total_order_amount` entry to match `customer_lifetime_value`.

For a durable fix, put the status-to-revenue decision in one place, such as a small `order_status_mapping` seed with an `is_completed` flag that both `customers` and any future model join to.

If the admin is the thing that's wrong, leave the models alone and fix the admin query instead.

Then run `dbt build --select customers`. The table is fully rebuilt, so no incremental state needs clearing. Expected result: `completed_lifetime_value` sums to 1103.00, and March completed revenue is 279.00.

## 3. Test

Every existing test checks internal consistency, and this bug is internally consistent. Only a check against an external figure would have caught it. Add a seed `finance_completed_revenue_by_month` (or just a single total) holding finance's reported numbers, then add a singular test:

```sql
-- tests/completed_revenue_matches_finance.sql
with model as (
    select sum(completed_lifetime_value) as total from {{ ref('customers') }}
),

finance as (
    select sum(completed_amount) as total from {{ ref('finance_completed_revenue_by_month') }}
)

select model.total as model_total, finance.total as finance_total
from model, finance
where abs(model.total - finance.total) > 0.005
```

With the current code this test fails (967.00 vs 1103.00). It needs updating when finance re-cuts its figures, but it fails on any future drift between the warehouse and the reported number, which is the failure mode here.

I haven't run `dbt` against this project. The root cause and expected numbers are derived by hand from the log and the sample output.
