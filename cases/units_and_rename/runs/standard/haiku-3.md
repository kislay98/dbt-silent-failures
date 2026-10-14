claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 46 s | USD 0.008 | 2026-10-15 | project ad0b16b | prompt 59943c0b3f30

## 1. Root cause

**The main defect is in the raw payments feed, and `stg_payments.sql` passes it through unchecked.**

- `raw_payments` now has two method columns: `payment_method` and `method`. For the March and April payments, `payment_method` is NULL and `method` holds the real value (for example, payment ids 112, 111, 90, 97 and 86 in the sample). The ingestion job appears to have moved the method into `method` for later loads. This is an inference from the columns and sample rows, so confirm it with the ingestion owner.
- `models/staging/stg_payments.sql` selects `payment_method` directly and does not fall back to `method`. The NULLs pass straight through to staging.
- `models/orders.sql` then does `sum(case when payments.payment_method = 'coupon' ... else 0 end)`. A NULL method matches no branch, so every per-method column becomes 0.00 while `total_amount` still sums every payment. That is why March totals 595.27 with 0.00 in every method column.
- The same NULLs break `fees`. The join to `raw_payment_methods` on `payment_method` finds nothing, so the coalesce gives 0 fee, which is why March and April fees are 0.0.

**Why the tests passed:**

- `models/staging/schema.yml` has `accepted_values` on `stg_payments.payment_method`. dbt's `accepted_values` ignores NULLs, so it passes. There is no `not_null` on that column.
- The `not_null` tests on `orders.*_amount` in `models/schema.yml` pass because the `else 0` turns every missing method into 0, never NULL. Those tests cannot see this bug.
- Nothing checks that the per-method columns add up to `amount`.

**Second defect: `daily_revenue` is stale, and it is not the source of the 595.27 figure.**

- `models/daily_revenue.sql` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. The max is probably 2018-04-09, so the rebuild window starts around 2018-04-06. March and most of April are frozen at whatever they held when first built.
- Daily revenue shows March 622.0 and April 139.0. Those match the processor settlement exactly, while `orders` shows 595.27 and 106.33 for the same orders. Order counts match (35 in March), so the difference is in payment amounts, not in which orders exist.
- The frozen rows have the older amounts. The model's comment says payments land a day or two late, which does not cover corrections made weeks later.

**Open item: the amount gap is not fully explained.** The current raw payments sum to 26.73 less than settlement in March and 32.67 less in April. I can't tell from these samples whether payment rows were dropped, duplicated, or restated in the feed. The coupon ledger (45 and 33) also doesn't match the gaps directly. Confirm this against the raw feed before closing the finance question.

## 2. Fix

**a. Staging (the actual fix):** in `models/staging/stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the feed writes the method to `method` in later loads; `payment_method` is NULL there
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

Also fix the feed so `payment_method` is populated at the source. The coalesce only protects the warehouse. Check that `payment_method` and `method` agree wherever both are populated (row 28 and row 12 do).

**b. Rebuild `daily_revenue` fully.** Either run `dbt run --select daily_revenue --full-refresh` once, or change the model to `materialized='table'`. It is 69 rows, so a table is cheap and removes the stale-window problem. I'd choose the table. This is a design change, so confirm with whoever owns the model.

**c. Reconcile amounts to settlement** for March and April (per order, raw `amount` vs the processor feed) before re-issuing the report. The fix above does not change the 595.27 vs 622.00 gap.

Once the fix is in, the March and April coupon totals should match the coupon ledger (45.00 and 33.00). If they don't, that points to the amount problem in 2c.

## 3. Test that would have caught it

**a. Stop NULL methods in staging.** Add to `models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

**b. Assert the per-method split adds up to the total.** Create `tests/assert_order_payment_methods_sum_to_amount.sql`:

```sql
-- every order's per-method amounts must add up to its total amount
select
    order_id,
    amount,
    coalesce(credit_card_amount, 0) + coalesce(coupon_amount, 0)
        + coalesce(bank_transfer_amount, 0) + coalesce(gift_card_amount, 0) as methods_total
from {{ ref('orders') }}
where abs(
    coalesce(credit_card_amount, 0) + coalesce(coupon_amount, 0)
        + coalesce(bank_transfer_amount, 0) + coalesce(gift_card_amount, 0)
    - coalesce(amount, 0)
) > 0.005
```

On the current data this returns every March and April order, so it fails until the staging fix is in. It would have caught the bug on the first nightly run.
