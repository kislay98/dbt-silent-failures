claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 32 s | USD 0.005 | 2026-10-14 | project bef6b69

## 1. Root cause

**`delivered` is treated as completed by the admin but not by the models.** The arithmetic matches exactly:

- March admin 279.00 = 143.00 (completed) + 136.00 (delivered). The sample shows `delivered` as 136.0 in March, and it's the only month with delivered orders.
- Jan–Mar admin 1103.00 = 967.00 + 136.00. The 967.00 is the sum of `completed` in `orders` (424 + 400 + 143), which equals `customers.completed_lifetime_value`.

The models disagree with the admin only because of the status filter in `jaffle_shop/models/customers.sql`:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

Only `completed` matches. `stg_orders.sql` and `orders.sql` pass `status` through unchanged, so the `delivered` orders carry their status into `orders` and are then excluded from `completed_lifetime_value`.

**Why every test passes:** the `accepted_values` tests in `jaffle_shop/models/schema.yml` (`orders.status`) and `jaffle_shop/models/staging/schema.yml` (`stg_orders.status`) both list `'delivered'`. Someone widened the list to accept the new value. The tests check that a status is valid, not what it means downstream. The customer table also reconciles internally with `orders` (967 = 967), so no consistency test fails. The disagreement is about the definition of "completed," and no dbt test encodes that definition.

I inferred that `delivered` should count as completed from the numbers alone. I haven't queried the raw rows or checked this with finance. If finance says `delivered` is *not* completed, the admin is what's wrong and the fix goes there.

## 2. Fix

Make the definition explicit in one place, following the Jinja style already used in `orders.sql`. In `jaffle_shop/models/customers.sql`:

```sql
customer_payments as (

    {% set completed_statuses = ['completed', 'delivered'] %}

    select
        orders.customer_id,
        sum(amount) as total_amount,
        sum(case when orders.status in ({{ "'" ~ completed_statuses | join("', '") ~ "'" }}) then amount else 0 end) as completed_amount

    from payments

    left join orders on
         payments.order_id = orders.order_id

    group by orders.customer_id

),
```

Also update the description in `jaffle_shop/models/schema.yml` for `completed_lifetime_value` to say "status completed or delivered."

Then rebuild the affected models: `dbt build -s customers+`. `daily_revenue` is unaffected, which is consistent with total revenue already matching the admin.

## 3. Test that would have caught it

The existing tests check only that a status is in an allowed list. What's missing is a check against an independent number. Add a finance reconciliation seed (`seeds/finance_completed_revenue.csv`, with `month` and `completed_amount` columns from the admin export) and a singular test:

`jaffle_shop/tests/assert_completed_revenue_matches_finance.sql`:

```sql
-- Monthly completed revenue must match the figures finance reconciles to the shop admin
with expected as (

    select
        cast(month as date) as month,
        completed_amount as expected_amount

    from {{ ref('finance_completed_revenue') }}

),

actual as (

    select
        date_trunc('month', order_date) as month,
        sum(case when status in ('completed', 'delivered') then amount else 0 end) as actual_amount

    from {{ ref('orders') }}

    group by 1

)

select
    expected.month,
    expected.expected_amount,
    actual.actual_amount

from expected
left join actual on expected.month = actual.month
where coalesce(actual.actual_amount, 0) != expected.expected_amount
```

This would have failed for March (279 expected, 143 actual) on the first run. It needs the finance seed to be refreshed each month, and the status list in the test should match the one in `customers.sql`. Keep the two in sync, or move the list into a `var` that both use.

## Other things I noticed (not verified)

- **`region_revenue` may fan out.** `raw_customer_regions` has an `updated_at` column, which suggests multiple rows per customer. The schema says "one row per customer," but there is no `unique` test on `customer_id`. Joining orders to regions on `customer_id` would multiply revenue for those customers. This doesn't explain the completed-revenue discrepancy, but it's worth checking with `select customer_id, count(*) from raw_customer_regions group by 1 having count(*) > 1`.
- **Documentation drift.** `jaffle_shop/models/schema.yml` documents `total_order_amount` on `customers`, but the model outputs `customer_lifetime_value`.
