claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 35 s | USD 0.143 | 2026-10-14 | project f769fdf | prompt 0e22716f6c28

# Diagnosis: `daily_revenue` missing 6 February orders

## 1. Root cause

The bug is in `models/daily_revenue.sql`, and it was triggered by new February rows in `seeds/raw_orders.csv` (and `seeds/raw_payments.csv`).

**What changed in the data.** The stock jaffle_shop seeds have 99 orders and 113 payments. Last night's run loaded 105 orders and 119 payments. Six orders, each with one payment, were added late (backfilled), and all of them have `ordered_at` dates in **February 2018**. The mismatch query shows them: 2018-02-03, 02-08, 02-13, 02-17, 02-22 and 02-26. Together they are 6 orders and 65.00 of revenue, which is exactly the gap (480 − 415 and 1737 − 1672). I'm inferring the specific order IDs (probably 100–105) because the seed CSVs weren't included. You can confirm them with the first query in the fix section.

**Why the model dropped them.** `daily_revenue` is incremental, and on each run it only rebuilds:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

That window is anchored to the newest date already in the table, which is in April 2018. It only works if late data arrives within 3 days of the newest order date. A row dated February that lands today is outside the window, so it is never selected, and its date is never deleted and re-inserted. `orders` is a full `table` rebuild, so it picked up the new rows. That is why the two models have always matched until now.

**Why nothing failed.** Every test on `daily_revenue` is `unique` or `not_null` on its own output. No test compares it against `orders`, so a table that is internally valid but incomplete passes.

## 2. Fix

**Repair the data now**, after optionally confirming which orders are missing:

```sql
select o.* from orders o
left join daily_revenue d using (order_date)
where o.order_date in ('2018-02-03','2018-02-08','2018-02-13','2018-02-17','2018-02-22','2018-02-26');
```

```bash
dbt build --select daily_revenue --full-refresh
```

**Prevent it from recurring.** Change the incremental filter so it also rebuilds any date whose source totals differ from what is stored. That covers late-arriving and edited orders regardless of date:

```sql
    {% if is_incremental() %}
    -- payments can land a day or two after the order, so the last three days are rebuilt on every run;
    -- orders can also be backfilled for older dates, so any date that no longer matches the source is rebuilt too
    where order_date >= (select max(order_date) - interval 3 day from {{ this }})
       or order_date in (
            select src.order_date
            from (
                select order_date, count(*) as orders, sum(amount) as revenue
                from orders
                group by order_date
            ) as src
            left join {{ this }} as tgt
                on src.order_date = tgt.order_date
            where tgt.order_date is null
               or tgt.orders <> src.orders
               or tgt.revenue is distinct from src.revenue
       )
    {% endif %}
```

This still scans all of `orders`. At this size (around 100 rows) it is simpler to switch to `materialized='table'` and drop the incremental logic altogether. If you need to keep it incremental at scale, add a load timestamp (`_loaded_at`) to the raw tables. Then the filter can become "dates of any order loaded since the last run", which avoids the full comparison.

## 3. A test that would have caught this

Add a reconciliation test at `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must agree with orders for every order date
with source as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

),

target as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(source.order_date, target.order_date) as order_date,
    source.orders as source_orders,
    target.orders as target_orders,
    source.revenue as source_revenue,
    target.revenue as target_revenue

from source
full outer join target
    on source.order_date = target.order_date

where source.orders is distinct from target.orders
   or abs(coalesce(source.revenue, 0) - coalesce(target.revenue, 0)) > 0.005
```

Against last night's tables, this test returns the six February rows, so `dbt build` would have failed. Since the test depends on `daily_revenue`, it runs after that model in every build.

---

**Side note, not the cause of this incident:** `raw_customer_regions` has an `updated_at` column, which suggests the feed can contain more than one row per customer. Its `customer_id` has no `unique` test. If a customer ever appears twice, `region_revenue` will double-count that customer's payments. Consider adding a `unique` test there, or deduplicating to the latest `updated_at`.
