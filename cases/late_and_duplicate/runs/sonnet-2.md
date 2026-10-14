claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 34 s | USD 0.081 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

There are two independent defects, and each one explains one of the gaps.

**A. Duplicate payment rows in `raw_payments` (seed, read by `models/staging/stg_payments.sql`). This explains 1917 vs 1737 (+180.00).**
- `orders.amount` is `sum(stg_payments.amount)` per order, and `stg_payments.total_amount` is also 1917.00, so the extra 180.00 is already in the payments.
- Order 1 shows 20.00 against a 10.00 charge. That is the same payment loaded twice under a new `id`.
- The backfill re-delivered payments. The new rows have fresh `id`s, so `unique_stg_payments_payment_id` passes, and nothing checks payments against orders.
- `orders` is still one row per order (`unique_orders_order_id` passes) because the aggregation happens in `order_payments`, so only the amounts are inflated, not the row count.
- The duplicates are not limited to February. January and March days are inflated too (for example 2018-01-01: 20 vs 10).
- I can't see the seed data, so I'm inferring that the duplicates share `order_id`, `payment_method` and `amount`, with a different `id`. The query below confirms it.

**B. `models/daily_revenue.sql` never picks up late-arriving orders. This explains 1686 vs 1737 (the missing 51.00, plus 6 missing orders).**
- The incremental filter is `order_date >= max(order_date) - 3 days`, taken from `{{ this }}`. The max is in April, so only the last few days of April are rebuilt.
- The backfilled February orders have old `order_date`s, so they are never inserted. The comparison query shows 6 orders in `orders` with 0 rows in `daily_revenue` (99 vs 105 orders), on 02-03, 02-08, 02-13, 02-17, 02-22 and 02-26.
- The comment says payments can land late, but the window is relative to the newest order date, not the load time. A backfill falls outside it.
- The January and March days where counts match but amounts differ are the opposite case. `daily_revenue` kept the old, correct values, and `orders` is now inflated by defect A.
- The numbers reconcile: 1917 − 1686 = 231 = 180 (duplicates) + 51 (missing backfilled orders). The correct figure is 1686 + 51 = 1737, which matches the settlement.

## 2. Fix

**Data:** remove the duplicate payments from `raw_payments`, keeping the lowest `id` per group. Check the candidates first, because a legitimate split payment could have the same method and amount:

```sql
select order_id, payment_method, amount, count(*) n, list(id) ids
from raw_payments
group by 1, 2, 3
having count(*) > 1
order by 1;
```

If `sum(amount)` after removing the extras is 173700 cents, the match is exact. Ask the loader owner for a processor transaction id so duplicates can be identified without guessing.

**`daily_revenue.sql`:** the table is tiny and there's no load timestamp to window on, so make it a full table and drop the incremental logic:

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

If it must stay incremental, filter on a load timestamp rather than `max(order_date)`, and run `dbt build --full-refresh -s daily_revenue+` now to repair the existing rows. The stale rows won't fix themselves.

**Also check `region_revenue`:** `raw_customer_regions` has `updated_at`, so it may hold history. It has 100 rows, which suggests one per customer, but there is no uniqueness test on `customer_id`. Any duplicate would fan out the join and inflate revenue (see test 3c).

## 3. Tests that would have caught this

a. **`tests/assert_daily_revenue_matches_orders.sql`** (catches defect B):
```sql
select o.order_date
from (select order_date, count(*) n, sum(amount) amt from {{ ref('orders') }} group by 1) o
left join {{ ref('daily_revenue') }} d using (order_date)
where o.n <> coalesce(d.orders, 0)
   or abs(o.amt - coalesce(d.revenue, 0)) > 0.005
```

b. **Duplicate payments** (catches defect A). Add to `stg_payments` in `staging/schema.yml`, assuming `dbt_utils` is installed:
```yaml
data_tests:
  - dbt_utils.unique_combination_of_columns:
      arguments:
        combination_of_columns: [order_id, payment_method, amount]
```
Use `severity: warn` if legitimate identical split payments exist. The better long-term test is `unique` on the processor transaction id once it's loaded.

c. **Seed uniqueness** (guards `region_revenue`). Add `unique` to `raw_customer_regions.customer_id`.

d. **Settlement reconciliation:**
```sql
-- tests/assert_orders_total_matches_settlement.sql
select 1
from (select sum(amount) as amt from {{ ref('orders') }})
where abs(amt - {{ var('settlement_total') }}) > 0.005
```
Pass `settlement_total` per period, or load finance's figures as a seed and compare per month.

I haven't run any of this, since I only had the pasted output and no project files or warehouse.
