claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 35 s | USD 0.147 | 2026-10-14 | project 8ee5734 | prompt 89287ed414a2

# Diagnosis: `daily_revenue` missed the February backfill

## 1. Root cause

The bug is in **`models/daily_revenue.sql`**, in the incremental filter:

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
{% endif %}
```

The lookback is based on the date the order was placed, not the date it was loaded. Last night's backfill added six orders dated in February. The table's `max(order_date)` is in April 2018 (customer 41's most recent order is 2018-04-07), so the run only rebuilt early April. The February rows were never selected again.

The raw data and `orders` are both correct. What's wrong is that six stored rows in `daily_revenue` are stale. Each is short by exactly one backfilled order:

| date | missing orders | missing amount |
|---|---|---|
| 02-03 | 1 | 12 |
| 02-08 | 1 | 9 |
| 02-13 | 1 | 15 |
| 02-17 | 1 | 6 |
| 02-22 | 1 | 20 |
| 02-26 | 1 | 3 |
| **total** | **6** | **65.00** |

That accounts for the full gap: 480 − 415 = 65 and 33 − 27 = 6. On 02-03 and 02-22 the backfilled order was the only order that day, so no row exists for those dates at all.

All tests passed because nothing compares `daily_revenue` against `orders`. The model's own tests (unique / not_null) are still true for stale rows.

The comment in the file assumes data arrives at most two days late. A store backfill breaks that assumption, and nothing in `raw_orders` records when a row was loaded, so the model has no way to detect late rows.

## 2. Fix

**Immediate repair** (rebuilds the whole table from `orders`):

```bash
dbt build --select daily_revenue --full-refresh
```

**Durable fix:** stop making this model incremental. It is one aggregate row per day over a table `orders` already rebuilds in full on every run. Incremental saves almost nothing here, and it is wrong whenever data arrives late.

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

If it must stay incremental at a larger scale, have ingestion add a load timestamp (e.g. `_loaded_at`) to `raw_orders` and `raw_payments`. Then rebuild every `order_date` touched by rows loaded since the last run, rather than a fixed window of order dates.

## 3. Test that would have caught it

Add a singular reconciliation test, `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must agree with orders for every order date
with expected as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(e.order_date, d.order_date) as order_date,
    e.orders  as expected_orders,
    d.orders  as actual_orders,
    e.revenue as expected_revenue,
    d.revenue as actual_revenue
from expected e
full outer join {{ ref('daily_revenue') }} d
    on e.order_date = d.order_date
where e.order_date is null
   or d.order_date is null
   or e.orders <> d.orders
   or abs(e.revenue - d.revenue) > 0.005
```

Because it refs both models, `dbt build` runs it after `daily_revenue`. Last night it would have failed with the six rows above.

## Separate issue: `region_revenue` is overcounting

While checking this, I noticed `region_revenue` sums to **1838.00** but `orders` totals **1737.00**, which is 101.00 too much. The most likely cause is `raw_customer_regions` holding more than one row per `customer_id`. Its `updated_at` column suggests it keeps history rows. Each extra row fans out the payments join.

Nothing tests `customer_id` for uniqueness on that seed. I'd add a `unique` test there, then either dedupe to the latest `updated_at` per customer in the model or fix the feed. The same kind of `orders`-vs-`region_revenue` total reconciliation test would catch it.
