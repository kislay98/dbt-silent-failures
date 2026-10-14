claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 44 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt 2b2290cfef14

## 1. Root causes

There are two separate bugs. The passing tests don't catch either, because none of them check that the numbers agree across models.

**A. Completed revenue: `models/customers.sql`, `customer_payments` CTE**

The CTE filters on `orders.status = 'completed'` only:

```sql
sum(case when orders.status = 'completed' then amount else 0 end) as completed_amount
```

But `delivered` is a valid status. It's in the `accepted_values` lists in `models/schema.yml` and `models/staging/schema.yml`, and it holds 14 orders worth 201.00. The admin's figure is exactly `completed_lifetime_value` plus delivered:

- All months: 967.00 + 201.00 = 1168.00, which matches the admin.
- March: the admin shows 279.00 and `orders` shows 143.00 completed. The gap is 136.00, which should be delivered orders in March. I can't confirm that from the output because no monthly status breakdown was provided.

So the model is internally consistent with its own definition, but finance counts `delivered` as completed. The description in `models/schema.yml` ("status completed") and the `orders_status` doc block (not provided) should settle which definition is intended. I'm treating the admin's definition as the intended one, which needs sign-off.

**B. February in `daily_revenue`: `models/daily_revenue.sql`, incremental filter**

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The model only reads `orders` rows whose `order_date` falls within three days of the newest date already in the target table. February orders that show up after the table already holds April data (for example, a backfill into `raw_orders`) are outside that window and are never inserted. `delete+insert` only touches the window, so they stay missing.

The data fits this exactly:
- Orders: 33 orders, 480.00. Daily revenue: 27 orders, 415.00.
- The gap is 6 orders and 65.00, which is the missing rows. Count differences are missing rows, not amount drift.

The comment says the lookback is for payments landing late, but the filter is on `order_date`, which is the wrong axis for late-arriving orders. I can't see the raw_orders load history, so this is the mechanism that fits the evidence. To confirm, list the February rows in `orders` that have no matching row in `daily_revenue`, and check their load timestamps against the last run.

## 2. Fix

**`models/daily_revenue.sql`:** drop the incremental logic. The model is 69 rows built from a table, so a full rebuild is cheap and can't drop late data.

```sql
{{ config(materialized='table') }}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

Run `dbt run --select daily_revenue` once. The config change makes dbt rebuild the table.

**`dbt_project.yml`:** define the completed statuses once so the models can't drift apart again. (This is my recommendation, pending finance sign-off.)

```yaml
vars:
  completed_statuses: ['completed', 'delivered']
```

**`models/customers.sql`:** use the variable instead of the hard-coded value.

```sql
sum(case when orders.status in ({{ "'" ~ var('completed_statuses') | join("', '") ~ "'" }})
         then amount else 0 end) as completed_amount
```

Update the `completed_lifetime_value` description in `models/schema.yml` to match. The `customers` description also lists `total_order_amount`, which the model no longer produces, so remove it while you're there.

## 3. Test that would have caught it

**Reconciliation test for `daily_revenue`.** Save as `tests/assert_daily_revenue_matches_orders.sql`. It returns any date where the two models disagree, so it fails on February today (27 vs 33 orders, 415.00 vs 480.00).

```sql
with expected as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

),

actual as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(e.order_date, a.order_date) as order_date,
    e.orders as expected_orders,
    a.orders as actual_orders,
    e.revenue as expected_revenue,
    a.revenue as actual_revenue

from expected e
full outer join actual a on e.order_date = a.order_date

where e.orders is distinct from a.orders
   or round(e.revenue, 2) is distinct from round(a.revenue, 2)
```

The existing `unique` and `not_null` tests can't see missing rows, which is why this passed.

**Status classification test.** The `accepted_values` test passed because `delivered` was in its list. Nothing checked that every status has a meaning in the revenue logic. Add a singular test that fails if any status in `stg_orders` is missing from `completed_statuses` and from an equivalent `non_completed_statuses` var. That would have flagged `delivered` the moment it was added.

## Notes

- I haven't run any of this. The conclusions come from the pasted outputs and the file contents.
- The status definition is a business decision. Confirm with finance before merging the `completed_statuses` change, since it changes `customers` numbers.
