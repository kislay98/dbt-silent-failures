claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 52 s | USD 0.227 | 2026-10-15 | project ad0b16b | prompt 5e3ee671b766

# daily_revenue lost the backfilled orders, and order 100's timestamp is shifted

There are two separate faults. The first explains the dashboard mismatch. The second explains why order 100 is dated 31 January.

## 1. Root cause

### A. `models/daily_revenue.sql`: the incremental window never revisits the backfilled dates

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

On each run, `daily_revenue` only rebuilds dates within 3 days of its own latest date, which is now early April. The six backfilled orders (ids 100–105) arrived last night but are dated 31 January to late February. Their dates fall outside the window, so `delete+insert` never touched them. Every test passed because the tests only check `unique` and `not_null`, and nothing compares the model against `orders`.

The output confirms it:

| month | `orders` | `daily_revenue` | gap |
|---|---|---|---|
| Jan | 30 / 502.00 | 29 / 496.00 | 1 order / 6.00 (order 100, the 600-cent coupon payment) |
| Feb | 32 / 474.00 | 27 / 415.00 | 5 orders / 59.00 |

The two gaps add up to 6 orders and 65.00, which is exactly the backfill. `daily_revenue` still holds January and February as they were before the backfill.

### B. `raw_orders`, ids 100–105: `ordered_at` is 5h30m too early

- The store's sync screen shows order 100 at **2018-02-01 04:48:19**.
- The warehouse has **2018-01-31 23:18:19**.
- The difference is −5:30.

The rest of `raw_orders` matches the shop admin's local dates, since ops reconciled every day without issues. So the ingestion job applied a timezone conversion to this store's backfill that it doesn't apply to the other rows. `stg_orders.sql` just does `cast(ordered_at as date)`, so the shift carries straight into `order_date`:

- Order 100 lands on 31 January instead of 1 February.
- Order 101 is dated 2 February in `orders`, but the screen lists 3 February. It was probably pushed back across midnight the same way.
- Orders 102 and 104 (8th and 22nd at 14:00 and 15:47) stay on the same day even after shifting.

This also explains why ops counts six February orders, while `orders` shows only five extra.

## 2. Fix

**Data:** correct the backfilled timestamps at the source. Fix the ingestion job, or the seed CSV if `raw_orders` is a seed, since it's `ref`'d. Then reload. As a one-off patch:

```sql
update raw_orders
set ordered_at = ordered_at + interval '5 hours 30 minutes'
where id between 100 and 105;
```

I've only verified the offset against order 100. Before applying the patch, check all six against the sync screen; they should land on 1, 3, 8, 13, 22 and 26 February. Also fix the ingestion job's timezone handling for this store, or the next sync will repeat the problem.

**Model:** `daily_revenue` is about 70 rows built from about 100 orders, so the incremental logic buys nothing and loses late data. Make it a plain table:

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

Also update its description in `models/schema.yml`, which currently says "Incremental, rebuilds the last three days on each run".

If it must stay incremental, it needs a full refresh whenever a backfill lands: `dbt build --full-refresh -s daily_revenue`. Even with a refresh, a date-based lookback will always miss data that arrives later than the window.

After both fixes, the two models should agree:

| month | orders | revenue |
|---|---|---|
| Jan | 29 | 496.00 |
| Feb | 33 | 480.00 (415 + 65) |

That matches what ops expects: six extra February orders worth 65.00.

## 3. Test that would have caught it

Add a reconciliation test at `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must agree with orders on every order_date
with expected as (

    select order_date, count(*) as orders, round(sum(amount), 2) as revenue
    from {{ ref('orders') }}
    group by order_date

),

actual as (

    select order_date, orders, round(revenue, 2) as revenue
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
   or expected.revenue is distinct from actual.revenue
```

Last night this test would have failed on 31 January and on each affected February date.

The timestamp shift can't be detected from warehouse data alone, because a shifted timestamp still looks valid. That check belongs in the ingestion job: compare each backfill's per-day counts and totals against the store's own export before loading.
