claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 62 s | USD 0.010 | 2026-10-14 | project ad0b16b | prompt 2b2290cfef14

## 1. Root cause

There are two separate problems. Neither is caught by the tests.

**A. Completed revenue excludes `delivered` orders (`jaffle_shop/models/customers.sql`, and `completed_lifetime_value` in `schema.yml`)**

The model is internally consistent. `customers.completed_lifetime_value` (967.00) equals the `completed` status total in `orders` (967.0 across 59 orders). Both filter on `status = 'completed'` only.

The admin's figure is 1168.00, and 1168 − 967 = 201, which is exactly the `delivered` total in `orders`. So the admin is most likely counting `delivered` as completed. The March gap fits the same pattern: 279 − 143 = 136 would need to be March `delivered` revenue. I can't verify that from the sample.

`accepted_values` in `schema.yml` and `staging/schema.yml` lists `delivered` as a valid status, so the test only checks membership. It says nothing about whether `delivered` is a completed sale. The `completed_lifetime_value` description ("status completed") also leaves this unresolved. Whether `delivered` should count is a business decision, and I'd confirm it with finance before changing the model.

**B. `daily_revenue` is missing late-arriving February orders (`jaffle_shop/models/daily_revenue.sql`)**

The incremental filter is:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

This only rebuilds dates near the newest date already in the table. If an order with an old `order_date` shows up after those dates were built, the filter never selects it again. February has 6 orders (33 in `orders`, 27 in `daily_revenue`) and 65.00 of revenue (480.00 vs 415.00) that were loaded late and never reached the table.

The comment ("payments can land a day or two after the order") describes a different problem. The model's order counts can only be wrong because of missing orders, not late payment amounts, and the 3-day window can't recover orders that arrive weeks later.

Other things I noticed:
- `raw_customer_regions` and `raw_payment_methods` are seeds in this project, but the build log doesn't run them. The 4 tests on those seeds (32 found, 28 run) were never executed in the nightly build.
- `customers` documents a `total_order_amount` column that the model doesn't produce.

## 2. Fix

**B (daily_revenue), the clear bug.** Rebuild the table once, then stop depending on the window. At 69 rows, a full rebuild is cheap:

```bash
dbt run -s daily_revenue --full-refresh
```

```sql
{{ config(materialized='table') }}

with orders as (

    select * from {{ ref('orders') }}

)

select
    order_date,
    count(*) as orders,
    sum(amount) as revenue

from orders
group by order_date
```

If the table grows large later, keep it incremental but rebuild from the source on a window that covers how late data can arrive, or add a periodic full refresh.

**A (completed revenue), only after finance confirms the definition.** If `delivered` counts as completed, change the filter in `customers.sql`:

```sql
sum(case when orders.status in ('completed', 'delivered') then amount else 0 end) as completed_amount
```

Then update the `completed_lifetime_value` description in `schema.yml` to match. Put the status list in one place (for example an `is_completed` column in `orders`) so the next report doesn't drift.

If `delivered` shouldn't count, the model is right and the admin's query needs fixing.

## 3. Tests that would have caught this

**Reconciliation test for `daily_revenue` (catches B).** Add `tests/assert_daily_revenue_matches_orders.sql`. It runs on every build and returns any date where the two tables disagree:

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
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue

from expected
full outer join actual
    on expected.order_date = actual.order_date

where expected.orders is distinct from actual.orders
   or round(expected.revenue, 2) is distinct from round(actual.revenue, 2)
```

This would have failed on the February dates.

**Status-meaning test (catches A).** The existing `accepted_values` test should be paired with an explicit classification. Add a singular test that fails if any status isn't mapped to a bucket (`completed`, `open`, `returned`) in a seed. Adding `delivered` without deciding which bucket it belongs to would then fail the build instead of silently changing the numbers.

**Finance reconciliation (catches both).** Load the monthly figures finance uses (the admin's numbers) as a seed and assert that completed revenue per month matches them. This would have flagged the 1168 vs 967 gap and the 279 vs 143 gap in March at the next build.

**Seed tests.** Make sure `dbt build` runs the seeds and their tests, or that the ingestion job runs their tests. Right now the 4 seed tests are silently skipped.

## What I couldn't verify

I only had the logs, schema, and sample output, so I haven't checked:
- The March `delivered` total (136), which would confirm the admin's status definition.
- Which 6 February orders are missing from `daily_revenue`, and whether they arrived late.

To confirm both, run:

```sql
select status, sum(amount) as amount
from orders
where order_date >= date '2018-03-01' and order_date < date '2018-04-01'
group by 1 order by 1;

select o.order_date, count(*) as orders_in_orders, sum(o.amount) as revenue_in_orders,
       d.orders as orders_in_daily, d.revenue as revenue_in_daily
from orders o
left join daily_revenue d on d.order_date = o.order_date
where o.order_date >= date '2018-02-01' and o.order_date < date '2018-03-01'
group by 1, 4, 5
order by 1;
```
