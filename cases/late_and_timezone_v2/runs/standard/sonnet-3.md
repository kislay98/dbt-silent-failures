claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 41 s | USD 0.105 | 2026-10-15 | project ad0b16b | prompt 5e3ee671b766

## 1. Root cause

There are two separate problems. Both are invisible to the schema tests, which only check not-null, unique and accepted values.

**A. `models/daily_revenue.sql` never picks up the backfill (the 415 vs 474 gap).**
- It is incremental with `unique_key='order_date'`, and its only filter is `order_date >= max(order_date) - 3 days`.
- The backfilled orders arrived last night, but they are dated in January and February. They fall far outside that window, so the incremental run skipped them.
- `daily_revenue` is missing exactly the late rows: January has 29 orders / 496.00 against 30 / 502.00 in `orders`, and February has 27 / 415.00 against 32 / 474.00. The missing amounts are 6 + 59 = 65.00 across 1 + 5 = 6 orders, which matches the backfill.
- The comment "payments can land a day or two after the order" justifies a lookback on order date. It does not cover data that arrives weeks late.

**B. `models/staging/stg_orders.sql` dates the backfilled orders in the wrong time zone (`orders` is still off by one order).**
- `cast(ordered_at as date)` takes the date of a naive timestamp, and `raw_orders.ordered_at` is a `TIMESTAMP` with no zone.
- Order 100 is at `2018-01-31 23:18:19` in the warehouse and `2018-02-01 04:48:19` on the sync screen. That is a +05:30 offset, so the backfilled store's rows were loaded as UTC while ops compares against shop-local dates.
- Order 100 therefore lands on 31 Jan instead of 1 Feb.
- Order 101 looks affected too. `orders` has it on 2 Feb, but the sync screen lists a 3 Feb order. I can't see its time of day, so that part is inferred.
- Once A is fixed, the correct February is 33 orders / 480.00 (415 + 65). The current `orders` gives 32 / 474.00 because order 100 (6.00) is still in January.
- `raw_orders` has no store or time zone column, so the model cannot correct this itself.

## 2. Fix

**For A**, make `daily_revenue` a plain table. It has 69 rows, so there is nothing to save by running it incrementally:

```sql
{{ config(materialized='table') }}
```

Then run `dbt build --full-refresh -s daily_revenue+`. If you want to keep it incremental, lookback must be driven by load time, not order date. That needs a `loaded_at` column on `raw_orders`. A full refresh is also needed once either way, to repair the existing rows.

**For B**, this is a data fix at ingestion. In order of preference:
1. Have the ingestion job load `ordered_at` consistently, and add a `store_id` or time zone column so the staging model can convert.
2. As a stopgap, add 5h30m to `ordered_at` for orders 100–105, but only after checking all six against the sync screen. I have only confirmed order 100 and inferred 101.

Then rebuild. Expected result:

| Month | Orders | Revenue |
|---|---|---|
| January | 29 | 496.00 |
| February | 33 | 480.00 |

`customers.first_order` and `most_recent_order` also use `order_date`, so they will shift for the affected customers.

## 3. Tests

**Reconciliation between the two models.** Save it as `tests/assert_daily_revenue_matches_orders.sql`. It would have failed on this load:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)
select coalesce(o.order_date, d.order_date) as order_date,
       o.orders as expected_orders, d.orders as actual_orders,
       o.revenue as expected_revenue, d.revenue as actual_revenue
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where coalesce(o.orders, 0) != coalesce(d.orders, 0)
   or coalesce(o.revenue, 0) != coalesce(d.revenue, 0)
```

**For B,** load the shop admin's daily order counts as a seed (for example `seeds/shop_admin_daily_orders.csv`) and diff them against `orders` by `order_date`. Ops already does this by hand, so the test automates their check. It would have flagged order 100 sitting on the wrong day.
