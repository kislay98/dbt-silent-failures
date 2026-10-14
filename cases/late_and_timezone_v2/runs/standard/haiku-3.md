claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 44 s | USD 0.008 | 2026-10-15 | project ad0b16b | prompt 5e3ee671b766

## 1. Root cause

**`models/daily_revenue.sql`**, the incremental filter. `orders` is rebuilt in full each night, so it picks up the backfill. `daily_revenue` only rebuilds `order_date >= max(order_date) - 3 days`, so the backfilled February rows are never re-aggregated. February keeps its old totals and the table still passes its tests, which only check `unique`, `not_null`, and nothing against `orders`.

The numbers reconcile exactly:

| Month | `orders` | `daily_revenue` | Difference |
|---|---|---|---|
| Jan | 30 / 502.00 | 29 / 496.00 | 1 order / 6.00 (order 100, a 600-cent payment, dated 31 Jan) |
| Feb | 32 / 474.00 | 27 / 415.00 | 5 orders / 59.00 |
| Total | 105 | 99 | 6 orders / 65.00 (the whole backfill) |

The 65.00 backfill is order 100 (6.00) plus five February orders (59.00). Order 100 is 31 January in both `raw_orders` and `orders`, so it never reaches the February window. All five February orders are missing from `daily_revenue`. The "3-day lookback for late payments" in the model no longer applies. `orders` is already a full rebuild, so payments that land late are picked up upstream. The window only causes damage when old rows arrive late.

**The timezone is a separate issue, not the cause of 415 vs 474.** `raw_orders` stores 23:18:19 UTC, which is 04:48:19 on 1 February at UTC+05:30. `orders` correctly dates it 31 January, matching its documented UTC convention. Ops reconciles against shop-local dates, so that one order will still look different to them. That is a definition question (see the note under fix 2), not a dbt bug.

## 2. Fix

**One-off repair of the existing table** (the data change):

```bash
dbt run --select daily_revenue --full-refresh
```

**Code fix** in `models/daily_revenue.sql`. The table is about 69 rows and `orders` is already a full table, so there is no reason to make it incremental:

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

Also update the description in `models/schema.yml`, which currently says "Incremental, rebuilds the last three days on each run". Remove that sentence.

**Timezone decision (ops, not code):** Decide whether `order_date` means UTC or shop-local. The current docs say UTC. If ops needs local dates, add a separate `order_date_local` column that uses each store's offset, rather than changing `order_date` silently. Confirm that the backfill's `ordered_at` uses the same UTC convention as the rest of `raw_orders`. I can only check this from the one pair of timestamps provided.

## 3. A test that would have caught it

Add `tests/assert_daily_revenue_matches_orders.sql`. It compares every day in the full history, not just the incremental window, so it fails on the first build after a backfill:

```sql
-- Fails if any day in daily_revenue disagrees with orders, across the whole history
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

where expected.orders is distinct from actual.orders
    or round(expected.revenue, 2) is distinct from round(actual.revenue, 2)
```

Against the current data this query returns 6 rows (the Jan 31 and Feb 1–26 days that are off). In the nightly log, the test would have shown as FAIL and the build would have reported an error, not "Completed successfully".

I haven't run any of this. The analysis is from the pasted data and the arithmetic above, and the repair and test should be run in your environment to confirm.
