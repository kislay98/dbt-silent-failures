claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 35 s | USD 0.005 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

The problem is in `jaffle_shop/models/daily_revenue.sql`. It is an incremental model, and its only lookback is the 3-day window on the target's own max date:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

Every incremental run rebuilds only the last three days. Any February row that was computed wrong, or that was never computed for a late-arriving order, is never revisited. `orders` is a full `table` rebuilt from the seeds on every run, so it reflects the current `raw_orders`. `daily_revenue` keeps whatever it wrote the last time those dates were in the window.

The data matches that:

- The mismatch is confined to 2018-02. Jan, Mar, and Apr match exactly. The window only covers the most recent dates, so February is outside it.
- Six February days each have exactly one order in `orders` that is missing from `daily_revenue` (33 vs 27 orders, 480 vs 415 revenue). The 65.00 gap is the same in the 1737 vs 1672 totals, so it is entirely these missing orders.
- `orders.amount` has no nulls (the `not_null` test passes), so this is not a payment-amount problem. It is a missing-rows problem.

The comment in the model ("payments can land a day or two after the order") is also wrong about the mechanism. The window is keyed on `order_date`, not on when anything landed. Rows that arrive or change with an older `order_date` are silently skipped.

Why nothing failed: every test is per-column (`unique`, `not_null`) or checks one model's values in isolation. No test compares `daily_revenue` back to `orders`, so a table that is internally consistent but stale passes everything.

Caveat: the files do not include the earlier `raw_orders` seed, so I can't confirm which orders were added or back-dated. The pattern is consistent with late-arriving February orders that landed after February's rows were last computed.

## 2. Fix

**Immediate data fix:** rebuild the table from scratch so February is recomputed from current `orders`:

```
dbt build --select daily_revenue --full-refresh
```

**Code fix:** the table is about 100 rows, so incremental logic buys almost nothing and creates this failure mode. Make it a plain table:

```sql
-- jaffle_shop/models/daily_revenue.sql
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

Also update the description in `jaffle_shop/models/schema.yml`. It currently says "Incremental, rebuilds the last three days on each run", which will be false after this change.

If you need incremental for scale later, the lookback has to be driven by a change timestamp on `orders` (such as a load timestamp) rather than by `order_date`, and you should still run periodic full refreshes.

**Verify:** rerun the monthly comparison query from the output sample. It should show 2018-02 at 33 orders and 480.0, and the 1737.0 vs 1672.0 totals should agree.

## 3. Test that would have caught it

Add a singular test at `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`. It fails if any day differs between `orders` and `daily_revenue`, including days present in only one of them:

```sql
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

),

compared as (

    select
        coalesce(expected.order_date, actual.order_date) as order_date,
        coalesce(expected.orders, 0) as expected_orders,
        coalesce(actual.orders, 0) as actual_orders,
        coalesce(expected.revenue, 0) as expected_revenue,
        coalesce(actual.revenue, 0) as actual_revenue

    from expected
    full outer join actual
        on expected.order_date = actual.order_date

)

select *
from compared
where expected_orders <> actual_orders
   or abs(expected_revenue - actual_revenue) > 0.005
```

This test would have failed on the run that first left February stale, with six rows in its output. It runs on every `dbt build`, so it doesn't depend on which days the incremental window happens to cover.

## Other things I noticed (not the cause here)

- `raw_customer_regions` has an `updated_at` column and only `not_null` on `customer_id`. If a customer can appear more than once, `region_revenue` joins on `customer_id` and will double-count revenue. Adding a `unique` test on `customer_id` in the seed's schema would catch that.
- `schema.yml` for `customers` documents `total_order_amount`, which the model doesn't produce. Harmless to the output, but misleading.
