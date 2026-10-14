claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 25 s | USD 0.005 | 2026-10-14 | project 8ee5734 | prompt 2e932ff37d63

## 1. Root cause

**`stg_payments.sql` reads `payment_method` from `raw_payments`, but for payments from March 2018 onward that column is NULL.** The method is in the `method` column, which `stg_payments` ignores.

- `raw_payments` has both `payment_method` and `method`. The ingestion job appears to have started writing the method to `method` around 1 March, leaving `payment_method` NULL. The staging sample shows this: orders 57 to 60 and the duplicate-looking payment 114 have NULL `payment_method`, while order 56 (28 February) has `credit_card`. I can't see the loader, so confirm this against the source.
- In `orders.sql`, a NULL method matches none of the `case when payments.payment_method = '...'` branches, so every per-method column is 0 for those orders. `amount` still sums the payment, which is why the monthly `amount` totals (708, 146) look plausible while the method columns are zero.
- The `fees` column is also wrong for these orders, since the seed join on `payment_method` gives a NULL fee rate that `coalesce` turns into 0.
- The tests pass for three reasons:
  - `accepted_values` on `stg_payments.payment_method` ignores NULLs.
  - `not_null` on the `*_amount` columns passes because the `case` returns 0, not NULL.
  - Nothing checks that the method amounts add up to `amount`.

**A second issue to check:** `stg_payments` has payment 68 and payment 114, both for order 59 at 28.00 with NULL method. Order 59's `amount` is 56.00, so it is probably a duplicated payment row from the loader. The `unique` test on `payment_id` can't catch this because the ids differ. If it is a duplicate, `customers`, `region_revenue` and `daily_revenue` are overstated too.

## 2. Fix

Confirm which column is authoritative before choosing the coalesce order. Run this on the raw table:

```sql
select
    count(*) filter (where payment_method is not null and method is not null and payment_method <> method) as conflicting,
    count(*) filter (where payment_method is null and method is not null) as method_only,
    count(*) filter (where payment_method is null and method is null) as neither
from raw_payments;
```

If `conflicting` is 0, use the coalesce below. Also check that the card volume for March from this query matches the processor's 334.00:

```sql
select sum(amount) from stg_payments
where payment_method = 'credit_card' and payment_date >= '2018-03-01' and payment_date < '2018-04-01';
```

Note: `stg_payments` has no date column, so join through `orders` on `order_id` to get the month.

**`jaffle_shop/models/staging/stg_payments.sql`:**

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the loader moved the method into `method` from March 2018; `payment_method` is NULL for those rows
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

If the duplicate in question 1 is confirmed (payment 114 is a re-delivery of 68), remove it at the source or dedupe in `stg_payments` once you know the rule for identifying it. Don't dedupe on `order_id` and `amount` alone without checking, since two real payments can match that.

**Rebuild:** after fixing staging, run `dbt build --select stg_payments+`. `daily_revenue` is incremental and only rebuilds the last three days, so if the duplicate fix changes older revenue, run `dbt build --select daily_revenue --full-refresh` too.

## 3. A test that would have caught this

Add a reconciliation check so a payment with an unrecognised or NULL method fails the build. Create **`jaffle_shop/tests/assert_orders_payment_methods_reconcile.sql`**:

```sql
-- every order's method amounts must add up to its total amount
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as methods_total
from {{ ref('orders') }}
where abs(coalesce(amount, 0) - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

This would have failed for orders 57 to 60 on the first nightly run. It also covers future unknown methods, which the `accepted_values` test misses when the value is NULL.

Also add `not_null` to `payment_method` in `jaffle_shop/models/staging/schema.yml`. Without it, the NULLs aren't flagged anywhere:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```
