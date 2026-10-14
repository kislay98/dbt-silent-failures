claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 63 s | USD 0.010 | 2026-10-14 | project ad0b16b | prompt 1c80550dffb3

## 1. Root cause

Two separate problems produce the two wrong numbers, and both trace back to the same backfill.

**A. Duplicate payment rows in `raw_payments` (seed data).** `stg_payments.sql` is a plain rename with no deduplication, so every row in `raw_payments` flows into `orders.amount`, `customers.customer_lifetime_value`, and `region_revenue.revenue`. All three downstream models total 1917.00, so they agree with each other and disagree with the processor. The gap of 180.00 is the duplicated money.

The evidence points to duplicate rows rather than wrong amounts:
- **Order 1 is 20.00, but the charge was 10.00.** That is what you get when one 1000-cent `credit_card` payment is loaded twice under two different `id`s, so `orders.amount` is 10 + 10.
- **Payment and order counts are higher than the source dataset this project is based on.** `raw_payments` has 131 rows and `raw_orders` has 105. The standard jaffle_shop seeds have 113 payments and 99 orders. I'm working from memory on those baselines, so check them, but the pattern fits: 6 extra orders (the February backfill) and about 18 extra payment rows, some for the backfilled orders and the rest duplicates on existing orders.
- **The `payment_id` unique test passes** because the duplicates were given new IDs. Nothing tests uniqueness on the business key `(order_id, payment_method, amount)`.

**B. `daily_revenue` never picks up the backfill (`daily_revenue.sql`).** The model is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. The latest order date is 2018-04-07, so only April 4 onward is rebuilt on each run. Everything older is frozen.
- The backfilled February orders are missing from the dashboard entirely: 99 orders in `daily_revenue` versus 105 in `orders`, and February shows 27 orders versus 33. That accounts for 100.00 of the gap.
- January (−74.00) and March (−57.00) have the same order counts as `orders`, but lower revenue. Those days were computed before the duplicate payments landed and were never rebuilt.
- 100 + 74 + 57 = 231 = 1917 − 1686. The dashboard gap is fully explained.

The `comment` in the model ("payments can land a day or two after the order") is no longer true for this data. The 3-day window cannot fix a backfill that reaches back weeks.

**Why the tests passed:** no test compares totals across models, compares `daily_revenue` to `orders`, or compares payments to the processor settlement. Every test checks a single column for nulls, uniqueness, or allowed values, and the data is clean on all of those.

I worked from the output you pasted and did not run queries, so the duplicate-row hypothesis needs confirming with the query in step 1 below.

## 2. Fix

**Step 1: find the duplicates and confirm they explain the 180.00.**
```sql
select order_id, payment_method, amount, count(*) as n, list(id order by id) as payment_ids
from raw_payments
group by order_id, payment_method, amount
having count(*) > 1
order by order_id;
```
Expect order 1 to appear. Check that the extra rows sum to 180.00 (divide `amount` by 100 for dollars). If they don't, some backfilled payments are not in the settlement and finance needs to say which ones.

**Step 2: remove the extra rows from the seed.** Edit `seeds/raw_payments.csv` to keep one row per real charge, then run:
```
dbt seed --select raw_payments
```
The seed is a full reload, so the fix is in the CSV, not in SQL. If the backfill came from a source table instead, fix it at the source and keep the seed as the copy.

**Step 3: stop `daily_revenue` from being incremental.** It has 69 rows, and `orders` is already a full table rebuilt every run, so incremental saves nothing here. Remove the incremental config and filter:
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
If you want to keep it incremental, the lookback must cover the oldest date a backfill can reach, and a 3-day window cannot do that.

**Step 4: rebuild everything, including the frozen rows.**
```
dbt build --full-refresh
```
Use `--full-refresh` at least for `daily_revenue` so existing stale rows are replaced.

**Expected results after the fix:** `sum(orders.amount)` = 1737.00, `customers.customer_lifetime_value` total = 1737.00, `region_revenue` revenue total = 1737.00, `daily_revenue` revenue total = 1737.00, and `daily_revenue` orders total = 105. The order count does not change, since duplicates add payments and not orders.

## 3. Tests that would have caught this

**Most important: a per-day reconciliation of the dashboard against orders.** Save as `tests/assert_daily_revenue_matches_orders.sql`:
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
full outer join actual using (order_date)

where actual.orders is distinct from expected.orders
   or abs(coalesce(actual.revenue, 0) - coalesce(expected.revenue, 0)) > 0.005
```
This fails on the current data for the six missing February orders and the stale January and March days. It would have failed on the first run after the backfill.

**Duplicate payments.** Save as `tests/assert_no_duplicate_payments.sql` with `severity: warn` in config. Split payments can legitimately repeat a method and amount, so a warning is more appropriate than an error:
```sql
{{ config(severity='warn') }}

select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by order_id, payment_method, amount
having count(*) > 1
```

**The test that matches what finance checks.** Load the processor settlement as a seed, then compare monthly totals of `stg_payments.amount` against it. This is the only test that catches wrong payments even when the models are internally consistent. It needs the settlement data in the warehouse, so it is the most valuable test to add, but it is also the one that depends on data I can't see here.

## Other things I noticed

- `customers` in `schema.yml` documents a `total_order_amount` column that the model doesn't produce. The model's column is `customer_lifetime_value`.
- `region_revenue` is correct only while `raw_customer_regions` has one row per customer. The seed has an `updated_at` column, which suggests a history table, and `customer_id` has no unique test. A fan-out would inflate region revenue silently. Add `unique` on `customer_id` in the seed's schema entry.
