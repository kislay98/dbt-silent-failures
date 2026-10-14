claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 44 s | USD 0.007 | 2026-10-14 | project f769fdf+wk6ext | prompt c5114118dec9

## 1. Root cause

There are two separate problems. Together they explain both symptoms.

**a) `daily_revenue` is missing six February orders (`models/daily_revenue.sql`).**

Orders 100–105 are dated 2018-02-03 to 2018-02-26 but have ids that follow the April orders (98, 99). They were loaded into `raw_orders` after the last `daily_revenue` build, so their `order_date` values fall outside the incremental window. The filter `order_date >= max(order_date) - 3 days` only rebuilds the last three days. February is never rebuilt, so those orders never reach the table.

The numbers match exactly. Those six orders are the only February orders missing from `daily_revenue`: 6 orders and 65.00 in revenue, which is 480.00 − 415.00 and 33 − 27. All six are `delivered`, and February's `delivered` total is also 65.00. The other months reconcile.

The comment in the file ("payments can land a day or two after the order") describes the wrong failure. The window is keyed on `order_date`, so it cannot catch backdated orders or late payments on older orders.

**b) The completed-revenue gap is `delivered` orders, not a data error (`models/customers.sql`).**

`customers.completed_lifetime_value` counts only `status = 'completed'`. The admin's 1168.00 is 967.00 plus 201.00. That 201.00 is:

- February `delivered`: 65.00
- March `delivered`: 136.00

March's admin figure, 279.00, is 143.00 completed plus 136.00 delivered. January has no delivered orders in the sample, so it matches. The admin is counting `delivered` as completed, and the warehouse does not. `customers.sql` follows its own schema description ("status completed"), and the accepted-values tests allow `delivered`, so nothing flagged the disagreement.

I'm assuming finance's definition is the correct one, since they are the ones reporting it as wrong. This is a business-definition decision and should be confirmed before the change in section 2 ships.

**Why every test passed:** the tests check column shape (unique, not_null, accepted values, relationships). None of them check that one model agrees with another or that a total matches an external figure.

## 2. Fix

**`models/daily_revenue.sql`.** The table is small, so drop the incremental logic and make it a plain table:

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

Then run `dbt build -s daily_revenue`. The table will be rebuilt in full, including February. Update the `description` in `models/schema.yml` to remove "Incremental, rebuilds the last three days on each run".

If you need to keep it incremental for volume reasons, the filter must not depend only on `order_date`. Use a lookback that covers the late-arrival window, or add a `--full-refresh` run after backfills.

**`models/customers.sql`.** If `delivered` should count as completed revenue, change the condition in `customer_payments`:

```sql
sum(case when orders.status in ('completed', 'delivered') then amount else 0 end) as completed_amount
```

Update the description in `models/schema.yml` to match, for example: "Total value (AUD) of a customer's orders with status completed or delivered". If finance's definition is completed-only, the change goes in the admin, not in this project.

## 3. Test that would have caught it

Add a singular test that fails if `daily_revenue` disagrees with `orders` on any day. This would have failed on the February rows, which is exactly the symptom Ops reported.

`tests/assert_daily_revenue_matches_orders.sql`:

```sql
with from_orders as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

),

from_daily as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily_revenue

from from_orders as o
full outer join from_daily as d
    on o.order_date = d.order_date

where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

The completed-revenue gap is a definition question, and no model-level test can settle it alone. To catch that class of problem, add a singular test that compares monthly completed revenue to a control total from finance (a seed of expected monthly figures). It would have failed in March (143.00 against 279.00) and in February. It only works once finance has agreed on the definition.

**Verification I haven't run:** I worked these numbers from the sample outputs and log, not from the warehouse. Before merging, run the test above and `dbt build -s daily_revenue`, then confirm February shows 33 orders and 480.00 in `daily_revenue`.
