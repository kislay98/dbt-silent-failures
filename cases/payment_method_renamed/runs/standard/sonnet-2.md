claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 18 s | USD 0.084 | 2026-10-14 | project ad0b16b | prompt 57278f2875bf

## 1. Root cause

The ingestion job changed the schema of `raw_payments`. It now has both `payment_method` and a new `method` column. Roughly from the start of March, new rows have `payment_method = NULL` and the real value in `method`. Older rows have both populated and identical. The evidence is in the `raw_payments` sample, where rows such as id 93 have `payment_method = NULL` and `method = coupon`, while id 28 has both set to `bank_transfer`.

`models/staging/stg_payments.sql` only reads `payment_method`, so it passes the NULLs through. In `models/orders.sql`, the pivot (`case when payments.payment_method = '...' then amount else 0 end`) matches none of the four methods for those rows. The same NULL fails the join to `raw_payment_methods`, so `fee_rate` coalesces to 0.

The result is that March and April orders keep the right `amount` (`sum(payments.amount)` ignores the method) but get 0.00 in every method column and 0.00 `fees`. That is why total revenue still matches settlement while the mix is wrong, for example 622.00 total against 0.00 by method.

Every test passed because none of them could see this:
- `accepted_values` on `stg_payments.payment_method` ignores NULLs.
- The `not_null` tests on `orders.*_amount` pass because the `else 0` in the pivot never yields NULL.
- Nothing compares the method columns to `amount`.

## 2. Fix

Coalesce the two columns in `models/staging/stg_payments.sql`:

```sql
    select
        id as payment_id,
        order_id,
        -- ingestion added `method` and stopped filling `payment_method` for new rows
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source
```

Then run `dbt build`. `orders` is a full table, so March and April, and `fees`, are corrected on rebuild. `daily_revenue` doesn't use method columns, so it is unaffected. I haven't run this, so please confirm March card volume comes out at 334.00 against the processor's statement.

Also ask the ingestion owner whether the rename is permanent. Once `payment_method` is dropped, the model will fail on a missing column, and that is the right failure.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add `not_null` to `stg_payments.payment_method`, which would have failed on the NULL rows:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Add a reconciliation test, `tests/assert_orders_method_amounts_sum_to_amount.sql`, which fails for every affected March and April order:

```sql
select order_id
from {{ ref('orders') }}
where amount is not null
  and abs(
        credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount
        - amount
      ) > 0.005
```
