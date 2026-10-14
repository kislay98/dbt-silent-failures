claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 32 s | USD 0.136 | 2026-10-14 | project f769fdf | prompt e8c77caade40

## 1. Root cause

**The data is correct. `models/daily_revenue.sql` is stale for February** because its incremental window never revisits those dates.

Some payments for February orders arrived in `raw_payments` weeks late, long after February had been aggregated. The ones you sampled have the highest payment ids (114, 115, 116…), which fits a late load:

| order | original payment | late payment | order_date diff in the sample |
|---|---|---|---|
| 30 | 35 (13.00) | **114** bank_transfer 5.00 | 2018-02-02: 30 vs 25 → **5** |
| 33 | 38 (22.00) | **115** bank_transfer 12.00 | 2018-02-04: 37 vs 25 → **12** |
| 36 | 41 (9.00) | **116** bank_transfer 8.00 | 2018-02-10: 55 vs 47 → **8** |

All eight mismatched February days add up to the gap: 5 + 12 + 8 + 15 + 6 + 9 + 11 + 4 = **70.00**. That is 485 − 415, and also equals the all-time gap of 1742 − 1672. Order counts match (27 = 27) because the orders were already there. Only their payment totals grew.

`orders` is a full `table` rebuild, so it picked up the late payments and matches the processor settlement (485.00). `daily_revenue` is `incremental` and only reprocesses:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`max(order_date)` is now 2018-04-09, so each run rebuilds only 2018-04-06 onward. The comment assumes payments arrive within "a day or two" of the order, and that is not true for this data. Also, the window is keyed on *order date*, not payment arrival, so a late payment for an old order is never picked up. `raw_payments` has no loaded-at timestamp, so the model cannot tell which dates were affected.

All tests pass because none of them compare `daily_revenue` to `orders`. `unique`/`not_null` are satisfied by stale numbers.

## 2. Fix

**Restore the data now:**

```bash
dbt build --select daily_revenue --full-refresh
```

**Prevent it from happening again.** The model is a small aggregate over a table that is fully rebuilt anyway, so the incremental logic saves almost nothing. Make it a table:

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

If it has to stay incremental, a longer lookback window won't solve it. You need to rebuild the dates whose payments changed. That requires the ingestion job to add a `loaded_at` column to `raw_payments`, so the model can reprocess every `order_date` that has a payment loaded since the last run. Until that column exists, a full table rebuild is the only correct option.

## 3. Test that would have caught it

`tests/assert_daily_revenue_reconciles_to_orders.sql`:

```sql
-- daily_revenue must equal orders aggregated by order_date; returns mismatching days
with expected as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(e.order_date, d.order_date) as order_date,
    e.orders as expected_orders,
    d.orders as actual_orders,
    e.revenue as expected_revenue,
    d.revenue as actual_revenue

from expected e
full outer join {{ ref('daily_revenue') }} d
    on e.order_date = d.order_date

where e.order_date is null
   or d.order_date is null
   or e.orders <> d.orders
   or abs(coalesce(e.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Against the current warehouse, this returns the 8 February dates above. It would have failed the nightly `dbt build` the first time the late payments landed.

---

**Side note, not related to February:** `raw_customer_regions` has an `updated_at` column, and the seed is described as "one row per customer" but only has `not_null` tests. If the feed contains several rows per customer, `region_revenue` will double-count. Adding a `unique` test on `customer_id` would confirm or rule that out.
