claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 22 s | USD 0.067 | 2026-10-14 | project 8ee5734 | prompt 89287ed414a2

## 1. Root cause

The cause is `models/daily_revenue.sql`, in the `is_incremental()` filter:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The lookback window is anchored to the latest `order_date` already in `daily_revenue`, which is April 2018, not to when the data arrived. On each incremental run only the last three days of order dates are deleted and rebuilt. The store's backfill added six completed February orders (65.00 in total). Those dates are about two months behind the watermark, so they fall outside the window, and `delete+insert` never touches those February rows.

The six rows in your diff query account for the gap exactly:

| order_date | missing orders | missing amount |
|---|---|---|
| 02-03 | 1 | 12 |
| 02-08 | 1 | 9 |
| 02-13 | 1 | 15 |
| 02-17 | 1 | 6 |
| 02-22 | 1 | 20 |
| 02-26 | 1 | 3 |
| **Total** | **6** | **65** |

This matches 33 − 27 orders and 480 − 415 in revenue. `orders` is a full table rebuild, so it picked up the backfill correctly.

The "payments can land a day or two after the order" comment assumes late data arrives near the newest order date. A multi-week backfill breaks that assumption.

Every test passed because the tests on `daily_revenue` only check `unique`/`not_null` on its own columns. Nothing reconciles it against `orders`.

## 2. Fix

**Data repair now:**
```
dbt run --full-refresh --select daily_revenue
```

**Code fix.** With about 100 orders, the aggregate is trivial to recompute, so the simplest correct fix is to drop the incremental logic and make it a plain table. In `daily_revenue.sql`, replace the config and remove the `is_incremental()` block:

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

If this model must stay incremental for scale, don't use a fixed window. Rebuild any date whose stored aggregate differs from the source, in addition to the recent days. Compute the aggregate in a CTE, then filter it to dates that are recent or missing/different in `{{ this }}`.

Also update the `daily_revenue` description in `schema.yml`, which currently says it "rebuilds the last three days".

## 3. Test that would have caught it

Add `tests/assert_daily_revenue_matches_orders.sql`, a singular test that returns any mismatching dates:

```sql
select
    o.order_date,
    count(*) as in_orders,
    coalesce(max(d.orders), 0) as in_daily_revenue,
    sum(o.amount) as orders_amount,
    coalesce(max(d.revenue), 0) as daily_revenue
from {{ ref('orders') }} o
left join {{ ref('daily_revenue') }} d using (order_date)
group by o.order_date
having count(*) <> coalesce(max(d.orders), 0)
    or abs(sum(o.amount) - coalesce(max(d.revenue), 0)) > 0.005
```

This is your diagnostic query turned into a test. It would have failed on the first night the backfill landed. A cheaper equivalent is `dbt_utils.equality` or a totals-level check, but the per-date version also tells you which dates are wrong.

## Separate issue, not verified

`region_revenue` totals 1838.00 revenue, while `orders` totals 1737.00 for the same 105 orders. `region_revenue` joins `raw_customer_regions` on `customer_id`, and that table has an `updated_at` column, which suggests multiple rows per customer. If so, the join fans out and inflates revenue. The `unique`/`not_null` tests defined on the seeds weren't in this log, so they didn't run. I'd check `select customer_id, count(*) from raw_customer_regions group by 1 having count(*) > 1`. Whether that explains the 101 difference needs the data, so I haven't confirmed it.
