claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 22 s | USD 0.064 | 2026-10-14 | project bef6b69

## 1. Root cause

`models/daily_revenue.sql` is incremental, and its rebuild window is too narrow for how payments actually arrive.

- The window is `order_date >= max(order_date) - 3 days`, and `max(order_date)` is 2018-04-09. Every run therefore rebuilds only the days after about 2018-04-06, and each older day keeps whatever value it had when it was first written.
- Some February orders received payments long after the order date. In the raw data these show up as payment ids 114, 115 and 116, far above the ids (35, 38, 41) of the same orders' original payments:
  - Order 30 has a credit card payment of 13 plus a late bank transfer (id 114) of 5.
  - Order 33 has a credit card payment of 22 plus a late bank transfer (id 115) of 12.
  - Order 36 has a bank transfer of 9 plus a late bank transfer (id 116) of 8.
- `orders` is a full table rebuild, so it picks these payments up. `daily_revenue` never revisits February, so it is frozen at the pre-backfill amounts.
- The model's comment, "payments can land a day or two after the order", is wrong for this data.

The 70.00 gap is exactly the sum of the late payments, spread over 8 February days: 5 + 12 + 8 + 15 + 6 + 9 + 11 + 4. The order counts match (27 on both sides) because no orders are missing, only the amounts. January and March agree because no late payments landed on those months.

The settlement figure of 485.00 matches `orders`, so `orders` is correct and `daily_revenue` is stale.

Every test passed because none of them compares `daily_revenue` to its source. `unique`/`not_null` on a stale-but-well-formed row are satisfied.

## 2. Fix

Immediate data fix:

```
dbt run --full-refresh -s daily_revenue
```

Durable fix: the aggregation is a trivial group-by over a small table, so the incremental model buys nothing and costs correctness. In `models/daily_revenue.sql`, change the config to:

```sql
{{ config(materialized='table') }}
```

and delete the `unique_key`, `incremental_strategy` and `is_incremental()` block.

If it has to stay incremental at scale, a fixed lookback of any size is only a band-aid. The proper approach is to add an ingestion timestamp (`loaded_at`) to `raw_payments` and rebuild every `order_date` that has a payment loaded since the last run. `raw_payments` has no such column today, so the ingestion job would need to add one.

## 3. Test that would have caught it

A singular test, `tests/assert_daily_revenue_matches_orders.sql`, that returns a row for any day where the two disagree:

```sql
with o as (
    select order_date, count(*) as orders, round(sum(amount), 2) as revenue
    from {{ ref('orders') }}
    group by order_date
),

d as (
    select order_date, orders, round(revenue, 2) as revenue
    from {{ ref('daily_revenue') }}
)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as expected_orders,
    d.orders as actual_orders,
    o.revenue as expected_revenue,
    d.revenue as actual_revenue
from o
full outer join d using (order_date)
where o.orders is distinct from d.orders
   or o.revenue is distinct from d.revenue
```

Against the current table it would have failed on the 8 February dates before finance found the gap.

One side note, not verified: `raw_customer_regions` has an `updated_at` column, which suggests it may hold more than one row per customer. If so, the join in `region_revenue` would inflate revenue. Run `select customer_id, count(*) from raw_customer_regions group by 1 having count(*) > 1` to rule it out.
