claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 18 s | USD 0.062 | 2026-10-14 | project 8ee5734 | prompt 2e932ff37d63

## 1. Root cause

`raw_payments` has two payment-method columns: the old `payment_method` and a new `method`. From March, the ingestion job fills only `method` and leaves `payment_method` NULL. The `stg_payments` sample shows this: orders 55 and 56 (February) have `credit_card`, while orders 57 onward (March) have NULL.

`models/staging/stg_payments.sql` only reads `payment_method`, so every March and April payment gets a NULL method. In `models/orders.sql`, each `case when payments.payment_method = '...'` is false, so the four method columns are 0 while `amount` (a plain `sum(payments.amount)`) is still right. That is why the totals of 708 and 146 are correct and the breakdown is all zeros.

The fees are also wrong for those months. The join to `raw_payment_methods` finds no match, so `coalesce(fee_rate, 0)` makes fees 0.

All tests pass because none of them can see this:
- `accepted_values` on `stg_payments.payment_method` ignores NULLs.
- `not_null` on the `orders.*_amount` columns passes because the sums are 0, not NULL.
- There is no `not_null` on `stg_payments.payment_method`, and no check that the method columns add up to `amount`.

## 2. Fix

First check what `method` contains, since I can't see its values:

```sql
select method, count(*) from raw_payments where payment_method is null group by 1
```

If the values use the same vocabulary (`credit_card`, `coupon`, `bank_transfer`, `gift_card`), change `models/staging/stg_payments.sql`:

```sql
    select
        id as payment_id,
        order_id,
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source
```

If `method` uses different spellings (for example `credit card` or `CARD`), map them to the four canonical values here. The `accepted_values` test will fail loudly if any are missed.

Then run `dbt build`. `orders` is a table, so it rebuilds in full, and the fees are corrected by the same change. `daily_revenue` only uses `amount`, which was never wrong, so it needs no backfill.

After the rebuild, March card volume should match the processor's 334.00. If it's off by exactly 28.00, look at payments 68 and 114: both are 28.00 on order 59, and 114 is out of sequence, so it may be a duplicate load. I can't tell from the data shown, so confirm it against the processor statement.

The ingestion job also needs to stop switching columns, or at least tell you when it does.

## 3. Tests that would have caught it

Add a `not_null` test on the method column in `models/staging/schema.yml`:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Add a singular test, `tests/assert_orders_payment_methods_sum_to_amount.sql`, that checks the breakdown reconciles with the total:

```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
```

Either test would have failed on the March data on the first run. The not_null test points straight at the cause. The reconciliation test would also catch a new, unmapped method value that has been silently dropped.
