claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 40 s | USD 0.007 | 2026-10-14 | project 8ee5734 | prompt 0a1f2799e0c1

## 1. Root cause

**`models/daily_revenue.sql` is incremental and never revisits February.** Lines 3-5 set `materialized='incremental'` with `delete+insert` on `order_date`, and lines 24-27 limit each run to:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`daily_revenue` already holds April rows, so `max(order_date)` is in April and the filter only touches the last few days. The backfill added orders for 31 Jan through 25 Feb. `orders` is a full table rebuild, so it picked them up. `daily_revenue` never recomputes those dates, and its existing February rows stay stale. The comment on line 25 assumes late data is recent (payments landing a day or two later), which doesn't hold for a backfill.

The data supports this:

- **The six backfilled orders are all in `orders`.** Revenue differs by 65.00: 6.00 on 2018-01-31 plus 59.00 across the five February dates (12 + 9 + 15 + 20 + 3). Orders differ by 6 (one in January, five in February).
- **January also drifts.** `orders` has 30 January orders and `daily_revenue` has 29, with 502.0 against 496.0. That's the same mechanism, not a second bug.
- **Dates line up with a UTC-to-local shift.** `stg_orders.order_date` is `cast(ordered_at as date)`, which is UTC per the schema docs. If the shop is about UTC+10 (my assumption, to check against the shop's timezone), the six local dates (1, 3, 8, 13, 22, 26 Feb) fall on UTC dates 31 Jan, 2, 8, 13, 22 and 25 Feb. Those are exactly the rows in the mismatch query. Confirm by checking `raw_orders.ordered_at` for these six orders.

Ops should know that a UTC `order_date` will never match the shop admin's local dates for orders near midnight. That's a reconciliation difference, not the cause of the Feb gap. The two models don't use different date logic, so the disagreement comes from the incremental window.

**Why the tests passed:** `daily_revenue` only has `unique` and `not_null` on its columns. Nothing checks that it agrees with `orders`, so a stale window goes unnoticed.

## 2. Fix

Rebuilding `daily_revenue` as a table is the simplest correct option. It's a single `group by` over `orders`, which is already a full table, so there's no cost to a full rebuild and no window to get wrong.

`models/daily_revenue.sql`:

```sql
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

The `config()` block is removed because `dbt_project.yml` already sets `+materialized: table` for models.

`models/schema.yml`, `daily_revenue` description: change "Incremental, rebuilds the last three days on each run" to "Orders and revenue per order date, rebuilt in full on each run".

After merging, run `dbt run --select daily_revenue --full-refresh` once so the February and January rows are corrected now rather than at the next nightly. Then rerun the reconciliation query and confirm it returns no rows.

## 3. Test that would have caught it

Add a singular test at `tests/assert_daily_revenue_matches_orders.sql`. It fails if any date differs between `orders` and `daily_revenue` on either count or revenue:

```sql
with order_totals as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily_revenue

from order_totals o

full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date

where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Against the current tables this returns the six mismatched dates and fails. It would have failed on the first nightly run after the backfill landed. Since `daily_revenue` is now a full rebuild, the test is a guard against future regressions, such as someone reintroducing an incremental filter.

I haven't run any of this; the diagnosis is from the logs, the output samples and the model code.
