claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 30 s | USD 0.005 | 2026-10-14 | project bef6b69

## 1. Root cause

The bug is in `jaffle_shop/models/daily_revenue.sql`. It is an incremental model that only rebuilds the last three days, so February's rows are frozen at the values they had when they were first built.

- `orders` is a full-refresh table, so it always reflects the current payments. February's 485.00 matches the settlement.
- `daily_revenue` only rewrites rows where `order_date >= max(order_date) - 3 days`. The table's max date is 2018-04-09, so the window starts in early April. February rows are never recomputed, and any payment that arrived for a February order after its first build never reaches the dashboard.
- The comment ("payments can land a day or two after the order") describes a lag the window can handle, but the evidence shows February payments arriving much later. Raw payments have no timestamp, so the model has no way to know which days need rebuilding.

The sample output supports this:

- The per-day differences sum to exactly 70.00 (485.00 − 415.00), and the 27 orders match on both sides.
- Three of the differing days line up exactly with the late-looking payment IDs (114, 115, 116):
  - 2018-02-02 is off by 5, which is payment 114 on order 30.
  - 2018-02-04 is off by 12, which is payment 115 on order 33.
  - 2018-02-10 is off by 8, which is payment 116 on order 36.
- The other five differing days (02-11, 02-16, 02-17, 02-21, 02-26; 45.00 in total) are probably other late payments. I inferred that from the pattern and did not query for it.

Why the tests pass: every test on `daily_revenue` is a per-row constraint (`unique`, `not_null`). None compares the output to `orders`, so stale but well-formed rows pass.

Nothing is wrong with the raw data, staging, or `orders`.

## 2. Fix

Make `daily_revenue` a full table. `orders` is already a full table, so the cost is trivial at this size, and this removes the incremental window entirely.

```sql
{{
    config(
        materialized='table'
    )
}}

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

Then run `dbt run --select daily_revenue` once to correct February. The next nightly build will keep it correct without any manual step.

If you need to keep it incremental for performance later, the window has to be driven by something that reflects late payments (for example, a payment ingestion timestamp), not by a fixed three-day lookback. Until the raw payments carry a load timestamp, a full rebuild is the only correct option.

Separately, I noticed a risk in `region_revenue`: it joins to `raw_customer_regions` on `customer_id`, but nothing enforces that `customer_id` is unique in that seed. The `updated_at` column suggests the feed may hold history, which would fan out orders and inflate revenue. Worth checking; I have not confirmed it.

## 3. Test that would have caught this

Add a singular test that reconciles `daily_revenue` against `orders` for every day. Singular tests run as part of `dbt build`, so this would have failed on the nightly run.

`jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`:

```sql
with orders_by_day as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

),

compared as (

    select
        order_date,
        o.orders as expected_orders,
        d.orders as actual_orders,
        o.revenue as expected_revenue,
        d.revenue as actual_revenue

    from orders_by_day o

    full outer join {{ ref('daily_revenue') }} d
        using (order_date)

)

select *
from compared
where expected_orders is distinct from actual_orders
   or round(expected_revenue, 2) is distinct from round(actual_revenue, 2)
```

This returns one row per mismatched day. On the current data it would return eight days, which matches the sample. It also catches the failure mode in general: any future drift between `orders` and `daily_revenue`, whatever the cause.
