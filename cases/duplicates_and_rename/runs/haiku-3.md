claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 22 s | USD 0.004 | 2026-10-14 | project 8ee5734 | prompt 2e932ff37d63

## 1. Root cause

The bug is in the data, not the models. In `raw_payments`, the ingestion job now has two payment-method columns: `payment_method` and `method`. Starting around order 57 (2018-03-01), `payment_method` is NULL and the value is in `method`. The sample confirms this: orders 57-60 have NULL `payment_method` in `stg_payments`.

The chain of failure:

- **`jaffle_shop/models/staging/stg_payments.sql`** selects only `payment_method`, so the NULLs pass straight through.
- **`jaffle_shop/models/orders.sql`** computes each method column with `sum(case when payments.payment_method = '...' ...)`. A NULL never matches, so every method column is 0 for those orders. `amount` is summed separately, which is why March and April still show 708.00 and 146.00. The `fees` column is also 0 for the same reason, because `coalesce(fee_rate, 0)` runs on NULL methods.
- **Why the tests pass:** `accepted_values` on `stg_payments.payment_method` in `jaffle_shop/models/staging/schema.yml` ignores NULLs, and there is no `not_null` test on that column. No test compares the per-method breakdown with `amount`, so nothing checks that the breakdown adds up to the total.

The sample also shows order 59 with two payments (ids 68 and 114, both 28.0), giving `amount` 56.0. This may be a genuine second payment or a duplicate from the ingestion job. I can't tell from the sample. It needs checking because it would also distort the card volume figure.

I'm working from the sample output and log only, so I haven't checked that `method` uses the same four values as `payment_method`.

## 2. Fix

First, confirm the source values before changing the model:

```sql
select method, count(*) from raw_payments where payment_method is null group by 1;
select count(*) from raw_payments where payment_method is null and method is null;
```

If `method` holds the same four values, fix the staging model so it falls back to `method`:

```sql
-- jaffle_shop/models/staging/stg_payments.sql
select
    id as payment_id,
    order_id,
    -- the ingestion job started writing the method to `method` for some rows (from 2018-03-01)
    coalesce(payment_method, method) as payment_method,

    -- `amount` is currently stored in cents, so we convert it to dollars
    amount / 100 as amount

from source
```

Then fix the upstream ingestion job so `payment_method` is populated again. The fallback only hides the change; the source should be corrected. After the rebuild, check that March's card volume matches the processor's 334.00. If it doesn't, investigate the duplicate payment for order 59 (id 114).

## 3. Tests that would have caught this

Add a `not_null` test to the staging model, in `jaffle_shop/models/staging/schema.yml`:

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

Add a reconciliation test that checks each order's method breakdown sums to its total. Save it as `jaffle_shop/tests/orders_methods_sum_to_amount.sql`:

```sql
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as methods_total
from {{ ref('orders') }}
where abs(coalesce(amount, 0) - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
```

The reconciliation test would have failed the nightly build on the first night the NULLs appeared, with every affected order listed. The `not_null` test would have pointed directly at `payment_method`.
