claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 39 s | USD 0.148 | 2026-10-14 | project f769fdf | prompt 0e22716f6c28

# Diagnosis: late-arriving February orders that the incremental model never reprocesses

## 1. Root cause

**Six February orders were added to the raw data after `daily_revenue` had already built February. The incremental filter in `models/daily_revenue.sql` never looks back far enough to pick them up.**

**The data:**
- `orders` has 105 rows and `daily_revenue` has 99. The whole gap is 6 orders worth 65.00, and all of it is in February.
- Your diff query shows each of the six dates is short by exactly one order: 02-03, 02-08, 02-13, 02-17, 02-22 and 02-26.
- The seed log shows `raw_orders` at 105 rows and `raw_payments` at 119. The stock jaffle_shop seeds have 99 orders and 113 payments. So most likely 6 orders and their 6 payments were appended to `seeds/raw_orders.csv` and `seeds/raw_payments.csv`, backdated to February.
- To confirm, run `select * from stg_orders where order_date in ('2018-02-03','2018-02-08','2018-02-13','2018-02-17','2018-02-22','2018-02-26') order by order_id`. The orders missing from `daily_revenue` should be the new IDs (likely 100–105).

**Why `orders` is right and `daily_revenue` is wrong:**
- `orders` is a `table` model, so it is rebuilt from scratch every run and includes the new rows.
- `daily_revenue` is `incremental` with this filter:
  ```sql
  where order_date >= (select max(order_date) - interval 3 day from {{ this }})
  ```
  `max(order_date)` in the existing table is in April 2018. Each run therefore only rebuilds the last few April days. February rows that arrived about two months late are never selected, and `delete+insert` never touches those dates.
- The model's comment assumes data arrives at most "a day or two" late. This feed broke that assumption.

**Why nothing failed:** every test on `daily_revenue` is a column-level `unique` or `not_null` check. Rows that are present but stale pass all of them. No test compares `daily_revenue` against `orders`.

## 2. Fix

**Repair the data now:**
```bash
dbt build --select daily_revenue --full-refresh
```
After this, February should show 33 orders / 480.00 and the total should be 1737.00.

**Prevent it from recurring.** I recommend dropping the incremental logic. The model aggregates about 100 rows, so incremental saves nothing, and it can silently drift from `orders` whenever data arrives outside the 3-day window:

```sql
-- models/daily_revenue.sql
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

Also update the description in `models/schema.yml`, since it still says "Incremental, rebuilds the last three days on each run".

If it has to stay incremental at real scale, the window needs to be driven by *when rows were loaded*, not by `order_date`. That means adding a `_loaded_at` column to the raw tables and filtering on it. The raw tables don't have one today. Until they do, schedule periodic `--full-refresh` runs and rely on the test below.

## 3. A test that would have caught this

`tests/assert_daily_revenue_reconciles_to_orders.sql`:
```sql
-- daily_revenue must match orders for every order_date: no missing, extra or stale days
with expected as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue
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

where expected.order_date is null
   or actual.order_date is null
   or expected.orders <> actual.orders
   or abs(coalesce(expected.revenue, 0) - coalesce(actual.revenue, 0)) > 0.005
```

Last night this test would have returned the six February dates and failed the build. Because it refs both models, `dbt build` runs it after `daily_revenue`.
