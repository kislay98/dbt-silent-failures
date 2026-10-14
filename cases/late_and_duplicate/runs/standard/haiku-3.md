claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 51 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt 1c80550dffb3

## 1. Root cause

There are two separate problems, and they explain the two different mismatches.

**a) The 180.00 overstatement in `orders` comes from the payments data, not the models.** `orders.amount` is `sum(stg_payments.amount)` per order, and the models don't fan out. The `customers` lifetime value (1917.00) and `region_revenue` revenue (93+375+374+134+161+455+325 = 1917.00) both match `orders` exactly, so every model is summing the same inflated payment rows. The likely culprit is the `raw_payments` seed (`seeds/raw_payments.csv`, loaded as `main.raw_payments`, 131 rows). The backfill probably wrote some payments twice with new `id`s. Order 1 shows 20.00 against a 10.00 charge, which fits a duplicated 10.00 payment. The existing `unique` test on `payment_id` can't catch this because the duplicates have different ids.

I can't see the payment rows themselves, so this is an inference. Coupon and gift card payments are internal (`processor = 'internal'` in `raw_payment_methods`), so finance's settlement might also exclude them. But the gap is 180.00 and those two methods total 410.00 (191.00 coupon + 219.00 gift card), so that alone doesn't explain it.

**b) The `daily_revenue` shortfall (1686.00) comes from the incremental logic in `models/daily_revenue.sql`.** The model only rebuilds `order_date >= max(order_date) - 3 days`. Any change to an older day never reaches the table. The differences from `orders` confirm this:

| Month | `orders` amount | `daily_revenue` | Difference | Order count (orders vs daily_revenue) |
|---|---|---|---|---|
| Jan | 570.00 | 496.00 | 74.00 | 29 vs 29 |
| Feb | 515.00 | 415.00 | 100.00 | 33 vs 27 |
| Mar | 679.00 | 622.00 | 57.00 | 35 vs 35 |
| Apr | 153.00 | 153.00 | 0 | 8 vs 8 |

The 6 missing February orders are the backfilled ones, and they sit outside the 3-day window. The January and March amounts changed with no change in order count, so existing orders were re-priced by late payments, and those days were never rebuilt. The three gaps add up to the full 231.00 difference (1917.00 − 1686.00).

## 2. Fix

**Data (the actual correction):**

1. Find the duplicates:
   ```sql
   select order_id, payment_method, amount, count(*) as n, list(id) as ids
   from raw_payments
   group by 1, 2, 3
   having count(*) > 1
   order by 1;
   ```
   Confirm that order 1 appears here and that the duplicates total 180.00 (18000 cents). Check the sums against the settlement file before deleting anything.
2. Remove the duplicate rows from `seeds/raw_payments.csv`, keeping one id per payment. Fix the upstream feed or backfill job too, or the next run will reintroduce them. Then run `dbt seed --select raw_payments`.

**Model (so `daily_revenue` stops drifting):**

Either rebuild it once and keep the incremental logic:
```bash
dbt run --select daily_revenue --full-refresh
```
or, since it is only about 69 rows, make it a plain table by removing the `incremental` config and the `is_incremental()` block. That removes the failure mode entirely.

Then run `dbt build --select +daily_revenue+` (or the whole project) and check that `sum(revenue)` in `daily_revenue` equals `sum(amount)` in `orders`.

## 3. Tests that would have caught this

**a) Duplicate payments (singular test, `tests/assert_no_duplicate_payments.sql`):**
```sql
select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```
This is a heuristic. A customer could legitimately make two identical payments on one order, so set it to `severity: warn` if that is possible in this business.

**b) Reconciliation with the settlement (the test that would have caught the 180.00 directly):**
Add a `settlement_totals` seed (period, processor, settled_amount) and a singular test that compares `sum(orders.amount)` against it, scoped to the same payment methods finance reconciles. Any mismatch fails the build. This is the only check here that compares the warehouse against an external source of truth, which is what finance actually cares about.

**c) Cross-model consistency (catches the `daily_revenue` drift):**
`tests/assert_daily_revenue_matches_orders.sql`:
```sql
select d.total_revenue, o.total_amount
from (select sum(revenue) as total_revenue from {{ ref('daily_revenue') }}) d
cross join (select sum(amount) as total_amount from {{ ref('orders') }}) o
where abs(d.total_revenue - o.total_amount) > 0.005
```
This would have failed on the stale 1686.00 table. It also makes the gap between the incremental model and its source visible on every run.
