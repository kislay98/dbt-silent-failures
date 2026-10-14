claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 39 s | USD 0.006 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

There are two separate problems. Together they explain both mismatches.

**a) Duplicate payment rows in `raw_payments` (orders overstated by 180.00).** `models/orders.sql` sums every payment row per `order_id` with no dedup, so any extra row inflates `orders.amount`. The evidence is order 1: `orders` shows 20.00 but the customer was charged 10.00, so the second 10.00 is most likely a duplicate row that the backfill inserted with a new `id`. The `unique` test on `payment_id` in `models/staging/schema.yml` passes because the duplicate has a fresh id. `131` payments across `105` orders, and `1917.00 - 1737.00 = 180.00` of excess, fit this. The seed data isn't here, so this is inferred, not confirmed. The query in section 2 will confirm it.

**b) `daily_revenue` never revisits old dates (dashboard understated by 231.00).** `models/daily_revenue.sql` only rebuilds rows where `order_date >= max(order_date) - 3 days` of the existing table. That window is anchored to the table's own maximum date, not to what changed. Three things follow:
- The six backfilled February orders are older than that window, so they never get inserted. `daily_revenue` has 99 orders, `orders` has 105, and the six missing orders are the February rows showing `in_daily_revenue = 0`.
- Earlier dates are frozen at whatever they were before the backfill. Order 1's 2018-01-01 row still holds the 10.00 charge, and every mismatched row has `daily_revenue` below `orders`. That is consistent with the table predating the duplicate and backfill rows.
- The comment about "payments landing a day or two late" describes a different problem from the one that actually occurs. Late-arriving backfills need a full recompute, not a three-day window.

The dashboard reads from `daily_revenue`, so it is wrong for two reasons: the missing rows and the stale values.

## 2. Fix

**Data (do first):** confirm the duplicates, then remove them after finance agrees on which row is real.

```sql
select order_id, payment_method, amount, count(*) as n, array_agg(id) as ids
from raw_payments
group by all
having count(*) > 1
order by order_id;
```

If the duplicate rows account for the 180.00, deleting them from `raw_payments` (or re-running the backfill with the bad rows removed) should bring `sum(stg_payments.amount)` to 1737.00. Check that before touching the models. Deleting rows from a raw table is hard to reverse, so get finance's confirmation first.

**Code, `models/daily_revenue.sql`:** the simplest correct model is a plain table. `orders` is already a table, so this costs little and removes the window entirely:

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

If you want to keep it incremental, you still need a one-time rebuild to repair the stored rows: `dbt build --select daily_revenue --full-refresh`. Without that, the frozen January-March rows stay wrong.

After both changes, `select sum(amount) from orders` should equal 1737.00, and `sum(revenue) from daily_revenue` should equal it too.

## 3. Test that would have caught this

Add a singular test at `tests/assert_daily_revenue_matches_orders.sql`. It compares each day in `daily_revenue` with `orders`, and it would have flagged all 16 mismatched dates in the output sample:

```sql
with by_day as (

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
    d.orders as orders_in_daily,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily

from by_day o

full outer join {{ ref('daily_revenue') }} d
    using (order_date)

where coalesce(o.orders, 0) <> coalesce(d.orders, 0)
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

The existing `unique` and `not_null` tests on `daily_revenue` could not detect this, since every row was unique and non-null.

A second test would catch the duplicates at their source. Add `tests/assert_no_duplicate_payments.sql` that fails on any `(order_id, payment_method, amount)` group with more than one row. Be aware this would flag legitimate repeated payments, so if those exist, key the test on the processor's transaction id instead. The strongest check is a reconciliation against the settlement total, which needs the settlement data loaded as a source.

**Other things I noticed:** `region_revenue` sums `stg_payments.amount` through a join to `orders`, so it inherits the duplicate problem. `models/schema.yml` also documents a `total_order_amount` column on `customers` that `customers.sql` does not produce.
