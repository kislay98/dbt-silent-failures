claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 38 s | USD 0.007 | 2026-10-15 | project ad0b16b | prompt 1b2ae9e32b52

## 1. Root cause

The models count only `status = 'completed'` as completed revenue. Orders with status `delivered` are left out, and the admin counts them.

The numbers match this exactly:

- **March:** admin 279.00 − orders completed 143.00 = **136.00**
- **Since January:** admin 1103.00 − `completed_lifetime_value` 967.00 = **136.00**
- The `delivered` orders in `orders` total **136.00** (8 orders), so the gap is exactly the delivered revenue. The March figure implies all delivered orders fall in March, which the sample is consistent with but doesn't prove.

Total revenue matches because `orders.amount`, `daily_revenue`, and `region_revenue` don't filter on status. Only `customers.sql` filters on status.

**File:** `jaffle_shop/models/customers.sql`, in the `customer_payments` CTE:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

**Data:** `delivered` is a valid status in `raw_orders` and `stg_orders`. Both `accepted_values` tests list it, so they pass, but they only check that the value is known, not how it is counted.

I'm assuming the admin is the reference, since finance is saying the dbt output is wrong. The `orders_status` doc block that `schema.yml` references wasn't included, so please confirm with finance that `delivered` counts as completed before changing the definition.

## 2. Fix

In `customers.sql`, count both statuses:

```sql
sum(case when orders.status in ('completed', 'delivered') then amount else 0 end) as completed_amount
```

Also update the description in `models/schema.yml` for `completed_lifetime_value` so it no longer says "status completed" only. After the rebuild, `customers.completed_lifetime_value` should sum to 1103.00.

If more models need completed revenue, define the list once (for example as a `completed_statuses` var in `dbt_project.yml`) so the definition can't drift between models.

To confirm the March gap before rebuilding:

```sql
select status, sum(amount) from orders
where status = 'delivered' and order_date >= '2018-03-01' and order_date < '2018-04-01'
group by 1;
```

It should return 136.00.

## 3. A test that would have caught it

Every existing test passes because the models agree with each other: `customers` and `orders` both use the same narrow definition. Only a check against an independent source would have flagged it. Finance's monthly completed-revenue control totals from the admin can serve as that source.

Add a seed `seeds/finance_completed_control_totals.csv` with `month` (date) and `completed_revenue` from the admin, then add this singular test:

```sql
-- tests/completed_revenue_matches_control_totals.sql
-- Returns a row for every month where dbt's completed revenue differs from finance's control totals
with dbt_completed as (

    select
        cast(date_trunc('month', order_date) as date) as month,
        sum(amount) as completed_revenue

    from {{ ref('orders') }}

    where status in ('completed', 'delivered')

    group by 1

),

control as (

    select
        month,
        completed_revenue

    from {{ ref('finance_completed_control_totals') }}

)

select
    coalesce(dbt_completed.month, control.month) as month,
    dbt_completed.completed_revenue as dbt_value,
    control.completed_revenue as control_value

from dbt_completed

full outer join control
    on dbt_completed.month = control.month

where abs(coalesce(dbt_completed.completed_revenue, 0) - coalesce(control.completed_revenue, 0)) > 0.005
```

With the original code, the March row would fail (143.00 vs 279.00). The test would also catch future drift if the admin's definition changes. It needs monthly control totals for every month, not only March, so ask finance for the full export.
