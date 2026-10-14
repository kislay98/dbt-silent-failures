claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 35 s | USD 0.005 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

There are two defects, and they compound. A third issue in `region_revenue` is out of scope for these numbers but should be fixed.

**(a) `daily_revenue.sql` never rebuilds the backfilled history (explains the dashboard gap).**

The incremental filter is:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The window is anchored on the max date already in the target table, not on what changed in the source. Historical rows outside the last three days are never recomputed, so backfilled February orders and any amounts changed after the original build stay stale. The output confirms this:

- Every mismatch is in January, February, or March. April, which is inside the window, matches exactly.
- February orders are 33 in `orders` but 27 in `daily_revenue`. Dates 2018-02-03 and 2018-02-22 are missing from `daily_revenue` entirely, which means the backfill rows were never inserted.
- January and March have equal order counts but different revenue (January 570 vs 496, March 679 vs 622). Those amounts changed after the day was built, so the rows are stale rather than missing.

The 3-day lookback comment ("payments can land a day or two after the order") describes the wrong problem. A backfill or a late payment can touch any date.

**(b) `orders.amount` is inflated by extra payment rows (explains the 1917 vs 1737 gap).**

`orders.sql` sets `amount = sum(all payments for the order)`. `stg_payments` has 131 rows for 105 orders, so 26 orders have more than one payment row. Order 1 shows 20.00 against a 10.00 charge, which means it has two rows summing to 20. The 180.00 excess over settlement is probably the duplicated rows.

Two explanations fit the data, and the data shown does not decide between them:

- **Duplicate rows from the backfill.** Same order, same method, same amount, but different `payment_id`. The `unique` test on `payment_id` passes because the ids differ.
- **Legitimate split tender.** Coupons or gift cards that the processor never settles. Finance's 1737 might be processor-only. If so, the model is right and the reconciliation basis is wrong.

Run this to tell them apart:

```sql
select p.order_id, p.payment_method, p.amount, p.payment_id, count(*) over (partition by p.order_id, p.payment_method, p.amount) as copies
from stg_payments p
where p.order_id in (select order_id from stg_payments group by order_id having count(*) > 1)
order by p.order_id;

-- split by method: does coupon + gift_card account for 180?
select payment_method, sum(amount) from stg_payments group by 1;
```

If `copies > 1` on the rows that add up to 180, it is duplication. If the 180 matches `coupon + gift_card`, it is a basis mismatch.

**(c) Side finding: `region_revenue` can fan out.** `raw_customer_regions` has an `updated_at` column, which suggests one row per customer per update. The schema test only checks `not_null` on `customer_id`, not `unique`. A customer with two region rows will double-count payments in `region_revenue`.

## 2. Fix

**Repair the data (once the duplicate check confirms it):**

- Back up `raw_payments`, then delete the duplicate rows, keeping the original `payment_id` for each duplicated group. Look at the rows before deleting.
- If the duplicates come from the backfill, fix the backfill job so it upserts on the source payment identifier instead of appending.

**Make `daily_revenue` correct:**

- Run `dbt build --full-refresh -s daily_revenue` now to repair the stale history.
- Change the materialization. The model is about 100 rows, so the incremental logic is not worth the risk:

```sql
{{ config(materialized='table') }}
```

Keep `order_date` grouping and drop the `is_incremental()` block. If you must keep it incremental, rebuild the dates that changed in the source, for example `where order_date in (select distinct order_date from {{ ref('orders') }} where ... changed ...)`, not a window off `max` in the target.

**Make the finance basis explicit.** If `amount` should be processor-settled only, add a column such as `settled_amount` in `orders` that sums only settled methods, and point the dashboard at that. Do not silently change `amount`.

**Dedupe region rows:** Pick the latest row per customer (`row_number() over (partition by customer_id order by updated_at desc)`) before the join, or add a `unique` test on `raw_customer_regions.customer_id` so the problem fails loudly.

## 3. A test that would have caught this

Passing tests on individual columns does not prove the totals reconcile. Add a singular reconciliation test in `tests/`:

```sql
-- tests/assert_daily_revenue_reconciles_to_orders.sql
select d.order_date, d.revenue, o.amount
from (select order_date, sum(amount) as amount from {{ ref('orders') }} group by 1) o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where abs(coalesce(d.revenue, 0) - coalesce(o.amount, 0)) > 0.005
   or d.orders <> (select count(*) from {{ ref('orders') }} x where x.order_date = o.order_date)
```

This returns every date where the dashboard disagrees with `orders`. It would have failed on the 16 mismatched dates in this build.

For the duplicate-payment problem, add a test on `stg_payments` that flags repeated (order_id, payment_method, amount) combinations. `dbt_utils.unique_combination_of_columns` on `(order_id, payment_method, amount)` set to `severity: warn` works, since legitimate split tender can repeat a method. Also add a finance-level check: compare `sum(orders.amount)` (or `settled_amount`) for the period against the processor settlement total in a seed, so a basis mismatch fails the build.
