claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 40 s | USD 0.159 | 2026-10-14 | project f769fdf | prompt f5f38ede3f4d

## 1. Root cause

The problem is in the **`raw_payments` data**, not the dbt code. Starting around 2018‑03‑20, the ingestion job loaded **coupon payments in whole dollars instead of cents**. `models/staging/stg_payments.sql` divides `amount` by 100, as it should for cents, so those rows end up 100× too small.

The numbers reconcile exactly:

| Month | Correct rows | Mis-scaled rows (shown / actual) | Corrected total | Ledger |
|---|---|---|---|---|
| March | #58: 18.00 | #76 0.02, #81 0.02, #86 0.23 → 0.27 / **27.00** | 18 + 27 = **45.00** | 45.00 ✓ |
| April | (none) | #92 0.02, #94 0.07, #95 0.24 → 0.33 / **33.00** | **33.00** | 33.00 ✓ |

- **March settlement:** 622.00 − 595.27 = 26.73 = 27.00 − 0.27. The whole March gap is these three coupon payments, so other payment methods in March are fine.
- **Before the cutoff:** order 58 (2018‑03‑01) shows 18.0, which is correct. Every coupon payment from order 76 onward is fractional, and all of them should be whole-dollar values (every other amount in the output is a whole number).

**Why every test passed:** the only tests on amounts are `not_null` and `accepted_values` on `payment_method`. Nothing checks scale or reconciles totals against anything external.

## 2. Fix

Fix the data at the source, not with a `CASE` hack in staging.

1. **Fix the ingestion job's coupon mapping** so it emits cents like the other payment methods.
2. **Repair the loaded rows.** Run a SELECT first and expect 6 rows with amounts 2, 2, 23, 2, 7, 24:
   ```sql
   select p.id, p.order_id, p.amount, o.ordered_at
   from raw_payments p
   join raw_orders o on o.id = p.order_id
   where p.payment_method = 'coupon'
     and o.ordered_at >= '2018-03-02'
   order by o.ordered_at;

   -- after confirming the ids:
   update raw_payments
   set amount = amount * 100
   where id in (/* the 6 ids from above */);
   ```
   If the ingestion job records a load batch or timestamp, filter on that instead of order date.
3. **Rebuild, including a full refresh of `daily_revenue`:**
   ```bash
   dbt build --full-refresh --select daily_revenue
   dbt build
   ```
   `daily_revenue` is incremental and only reprocesses the last 3 days. A normal run will never correct the March and April 2018 rows already in it. The `orders`, `customers` and `region_revenue` tables rebuild on every run.

After the fix, March `sum(amount)` should be 622.00, matching the settlement.

## 3. Tests that would have caught it

**Scale sanity check.** In this dataset every payment is a whole dollar amount, and a sub-dollar payment is almost certainly a unit error. Add `tests/assert_stg_payments_amount_scale.sql`:
```sql
-- raw amounts are in cents; a sub-dollar payment means a row was loaded in dollars
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount < 1
```
This would have flagged all 6 rows. If sub-dollar payments are legitimately possible, use `severity: warn`, or use a stricter rule such as `amount * 100 % 100 <> 0` if coupons are always whole dollars.

**Reconciliation test (the stronger catch).** Load the processor settlement and the coupon redemption ledger as sources. Then add a singular test that compares monthly `sum(orders.amount)` and `sum(orders.coupon_amount)` against them within a small tolerance. This catches any unit, duplication or missing-row error, not just this one.

## Side issues I noticed (not the cause here)

- **Seed tests aren't running.** `raw_customer_regions` has `not_null` tests, but the build ran 27 of 29 tests because the seeds aren't built.
- **Possible duplicate regions.** The table has an `updated_at` column and no `unique` test on `customer_id`. If it holds history rows, `region_revenue` will double-count. Worth adding a `unique` test.
- **Doc/column mismatch.** `schema.yml` documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`.
