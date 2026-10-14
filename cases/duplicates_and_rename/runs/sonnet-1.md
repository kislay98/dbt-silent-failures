claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 22 s | USD 0.065 | 2026-10-14 | project 8ee5734 | prompt 2e932ff37d63

## 1. Root cause

**File:** `raw_payments` (the ingestion job's output), consumed by `models/staging/stg_payments.sql`.

`raw_payments` has two method columns, `payment_method` and `method`. Starting at payment 65 (the first March order, 57), the loader writes the method into `method` and leaves `payment_method` NULL. That fits a feed schema change or a loader rename, and Jan/Feb rows still use `payment_method`.

`stg_payments` only reads `payment_method`, so every March/April payment has a NULL method. In `orders.sql`, each `case when payments.payment_method = '...'` is false for a NULL, so all four method columns sum to 0 while `amount` (`sum(payments.amount)`) is still correct. That is why the totals are right (708.00 and 146.00) and the mix is 0.00.

**Why the tests passed:**
- `accepted_values` on `stg_payments.payment_method` ignores NULLs, so it passes.
- `not_null` on the `orders.*_amount` columns passes because `sum(case ... else 0 end)` is never NULL.
- Nothing checks that the method amounts add up to `amount`, and `stg_payments.payment_method` has no `not_null` test.

**Side effect:** the `left join` to `raw_payment_methods` on a NULL method never matches, so `coalesce(fee_rate, 0)` gives 0 fees for March/April. `orders.fees` is understated for those months too.

**Caveat:** payments 68 and 114 are both order 59, 28.00 each, with different ids. That may be a legitimate split payment or a duplicate load. I can't tell from this sample, so check it against the processor statement. It doesn't cause the zeros.

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

Then run `dbt build --full-refresh -s stg_payments+`. The full refresh matters because `daily_revenue` is incremental, although its amounts don't depend on the method, so it should be unaffected either way.

I haven't seen the actual values in `method`. If they use a different vocabulary (for example `card` or `CC`), the existing `accepted_values` test will now fail on them, and you'd add a mapping `case` in the staging model. Also ask the ingestion owner whether `method` is the new permanent column, so the loader gets fixed or documented rather than relying on the coalesce forever.

## 3. Tests that would have caught it

Add the missing `not_null` in `models/staging/schema.yml`:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Add a reconciliation test, `tests/assert_orders_payment_mix_reconciles.sql`:

```sql
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as mix_total
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

This returns the 40+ March/April orders today and would have failed the nightly build. The `not_null` test catches the root cause at the staging layer, and the reconciliation test catches any future unmapped or misspelled method that the `accepted_values` test lets through.
