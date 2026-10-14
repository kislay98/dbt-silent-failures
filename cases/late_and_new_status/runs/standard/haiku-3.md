claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 48 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt 2b2290cfef14

## 1. Root cause

There are two separate bugs. Each one matches a different symptom.

**A. Completed revenue excludes `delivered` orders** (`jaffle_shop/models/customers.sql`, `customer_payments` CTE)

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

The data shows that the admin counts `delivered` as completed:

- `orders` completed = 967.00 (59 orders)
- `orders` delivered = 201.00 (14 orders)
- 967.00 + 201.00 = **1168.00**, which is exactly the admin figure since January.

The `delivered` status is new to the data. The accepted_values lists in `models/schema.yml` and `models/staging/schema.yml` already include it, so the status tests pass. Nothing classifies the status, so the model silently treats it as "not completed." The March gap (279.00 vs 143.00, difference 136.00) should also be delivered orders, but the sample doesn't show that breakdown, so run the query in section 3 to confirm.

**B. `daily_revenue` is missing February orders** (`jaffle_shop/models/daily_revenue.sql`, incremental filter)

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The February gap is 6 orders and 65.00 (33 orders / 480.00 in `orders`, 27 / 415.00 in `daily_revenue`). The total gap is 105 − 99 = 6 orders, so these orders are absent from the table, not just zero-valued. The orders have `amount` populated, since `not_null` passes.

The incremental predicate only rebuilds the last three days of `order_date`, measured from the table's current max. If the ingestion job loaded February orders after the table had already advanced into March, those orders fall outside every future window and are never picked up. The comment ("payments can land a day or two after the order") describes late payments on recent orders. It doesn't cover late-arriving orders with older dates, and it doesn't cover late payments on old orders either.

I inferred this from the window logic and the arithmetic. I couldn't see the ingestion load timestamps, so the query in section 3 should confirm it.

## 2. Fix

**customers.sql**: treat `delivered` as completed, then document the rule. Confirm with finance first that `delivered` is meant to count, because the status documentation (`orders_status`) wasn't included.

```diff
-        sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
+        sum(case when orders.status in ('completed', 'delivered') then amount else 0 end) as completed_amount
```

Also update `completed_lifetime_value` in `models/schema.yml` to say "status completed or delivered." Don't widen the accepted_values list again without deciding how each new status is classified.

**daily_revenue.sql**: the table has about 69 rows and rebuilds in well under a second, so make it a plain table like the other marts. This removes the bug class entirely.

```diff
 {{
     config(
-        materialized='incremental',
-        unique_key='order_date',
-        incremental_strategy='delete+insert'
+        materialized='table'
     )
 }}
@@
     from orders
-
-    {% if is_incremental() %}
-    -- payments can land a day or two after the order, so the last three days are rebuilt on every run
-    where order_date >= (select max(order_date) - interval 3 day from {{ this }})
-    {% endif %}
-
     group by order_date
```

Also update the `daily_revenue` description in `schema.yml`, which still says "Incremental, rebuilds the last three days."

**Repair the data now**: after the change, run `dbt run -s daily_revenue` (a table rebuild is already a full rebuild). If you keep the model incremental, run `dbt run --full-refresh -s daily_revenue` instead.

## 3. Test that would have caught this

**For B (daily_revenue drift)**: a singular test that reconciles the mart against its source. The current `unique` and `not_null` tests can't detect missing rows.

```sql
-- tests/assert_daily_revenue_matches_orders.sql
with from_orders as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_expected,
    d.orders as orders_actual,
    o.revenue as revenue_expected,
    d.revenue as revenue_actual
from from_orders o
full join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or o.revenue is distinct from d.revenue
```

This would have failed on the February rows.

**For A (status classification)**: the accepted_values test only checks that a status is known, so it passed for `delivered` too. Add a status-classification seed (`status`, `is_completed`) and a `relationships` test from `stg_orders.status` to it. A new status then fails the build until someone classifies it, and `customers.sql` reads `is_completed` instead of a hard-coded string.

## Verification queries

Run these before applying the fixes:

```sql
-- Which days differ between orders and daily_revenue
with o as (select order_date, count(*) as orders, sum(amount) as revenue from orders group by 1)
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders, o.revenue, d.revenue
from o full join daily_revenue d on o.order_date = d.order_date
where o.orders is distinct from d.orders or o.revenue is distinct from d.revenue
order by 1;

-- Completed vs completed+delivered by month (March should show 143 vs 279)
select date_trunc('month', order_date) as month,
       round(sum(case when status = 'completed' then amount end), 2) as completed,
       round(sum(case when status in ('completed', 'delivered') then amount end), 2) as completed_or_delivered
from orders group by 1 order by 1;
```

If the first query shows the six February orders, check their ingestion load timestamps. If they arrived after the table's last build, that confirms bug B.

## Other observations

- The build log shows "Found 32 data tests" but runs 28 of them. The four seed tests on `raw_customer_regions` and `raw_payment_methods` were skipped, probably because seeds are excluded from the nightly selection. Check that the job selects what you expect.
- `customers` documents a `total_order_amount` column that doesn't exist in the model. This is documentation drift and doesn't affect the numbers.
