claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 18 s | USD 0.084 | 2026-10-14 | project ad0b16b | prompt 57278f2875bf

## 1. Root cause

The ingestion job changed the shape of `raw_payments`. It now has a second column, `method`, and the later rows carry the payment method only there, with `payment_method` left NULL. Early rows (e.g. payment 28, 12, 57) have both columns populated. The later rows (payments 65, 74, 86, 90, 93, 97, 111, 112, ...) have `payment_method = NULL` and the real value in `method`. By order and payment ids, the cutover falls around 1 March, so March and April are affected and January and February are fine.

`models/staging/stg_payments.sql` still reads only `payment_method`, so the NULLs flow through to `stg_payments` (visible in the sample). Downstream:

- `models/orders.sql` pivots with `case when payments.payment_method = '...'`. A NULL matches none of the four methods, so every per-method column is 0 for those orders.
- Revenue is still right because `amount` is `sum(payments.amount)` regardless of method. That is why the March total of 622.00 matches the settlement while the mix is all zeros.
- `fees` is also understated. The join to `raw_payment_methods` finds no match, so `coalesce(fee_rate, 0)` gives 0. March and April show 0.00 fees.

Every test passes because none of them can see it:

- `accepted_values` on `stg_payments.payment_method` ignores NULLs.
- The `not_null` tests on the `*_amount` columns pass because the pivot returns 0, not NULL.
- There is no `not_null` on `payment_method` and no check that the method columns add up to `amount`.

## 2. Fix

In `models/staging/stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

Then run `dbt build`. `orders` and `customers` are tables, so no full refresh is needed.

- `daily_revenue` doesn't depend on payment method, so it is unaffected.
- March credit card should now come out at the processor's 334.00. I can't confirm that from the samples, so check it after the rebuild.
- April and the March and April fees will also correct themselves.

Longer term, ask the ingestion owners whether `method` replaces `payment_method`, and agree on one column. The `coalesce` is a stopgap that handles both.

## 3. Tests that would have caught it

Add to `models/staging/schema.yml`, since `accepted_values` skips NULLs:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Add a singular test, `tests/assert_payment_methods_sum_to_amount.sql`, which checks that the mix reconciles to the order amount:

```sql
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where amount is not null
  and abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

Before the fix, both tests fail on the March and April orders. The second would also catch any future unexpected method value that the pivot silently drops.
