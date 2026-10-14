claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 23 s | USD 0.171 | 2026-10-14 | project ad0b16b | prompt 57278f2875bf

# Payment mix diagnosis: March and April orders show 0.00 by payment method

## 1. Root cause

**The data:** `raw_payments` now has two columns for the payment method. One is the old `payment_method`, and the other is a new `method` column that the ingestion job adds. From March 2018 onward, the feed fills only `method` and leaves `payment_method` NULL. You can see this in the sample:

| id | order_id | payment_method | method |
|---|---|---|---|
| 57 | 51 (2018‑02‑24) | credit_card | credit_card |
| 65 | 57 (2018‑03‑01) | **NULL** | credit_card |
| 90 | 79 | **NULL** | credit_card |
| 112 | 98 (April) | **NULL** | bank_transfer |

Older rows have both columns filled. Newer rows have only `method`.

**The file:** `jaffle_shop/models/staging/stg_payments.sql` still selects `payment_method` and never reads `method`. As a result, every payment from March onward reaches `stg_payments` with `payment_method = NULL`. In `jaffle_shop/models/orders.sql`, that NULL has three effects:

- Every `case when payments.payment_method = '...'` evaluates to false, so all four `*_amount` columns are 0.
- The left join to `raw_payment_methods` finds no match, and `coalesce(fee_rate, 0)` makes `fees` 0.
- `sum(payments.amount)` does not depend on the method, so `amount` stays correct. This is why monthly totals still match the settlement.

April has the same problem: its total is 139.00, but every method column is 0.

**Why the tests passed:**

- `accepted_values` on `stg_payments.payment_method` ignores NULLs, because `NULL not in (...)` is not true.
- No test requires `payment_method` to be non-null.
- The `not_null` tests on `orders.*_amount` pass because the CASE expression returns 0, not NULL.
- No test checks that the method amounts add up to `amount`.

## 2. Fix

Read the new column in `jaffle_shop/models/staging/stg_payments.sql`. Keep the old one as a fallback for history:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the feed moved the method from `payment_method` to `method` in March 2018
        coalesce(method, payment_method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

Then run `dbt build`. `orders` is a table, so it rebuilds in full. `daily_revenue` only uses `amount`, so it doesn't need a `--full-refresh`.

Once the fix is in, March `credit_card` should reconcile to the processor's 334.00. March fees will also come back, at 334 × 0.029 plus the bank transfer fees. I haven't run this against your warehouse, so please confirm that number after the rebuild.

You should also raise this with the ingestion or source owner. A column rename that shows up as a new column, with NULLs in the old one, is a silent breaking change.

## 3. Tests that would have caught it

**a) Require the method to be present.** Add this to `models/staging/schema.yml`:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

**b) Check that the payment mix reconciles to the total.** This catches the bug no matter what the cause is. Save it as `tests/assert_orders_payment_mix_reconciles.sql`:

```sql
-- the per-method amounts must add up to the order total
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where abs(coalesce(amount, 0)
          - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
```

Against the current data, test (a) fails on every payment from March onward. Test (b) fails on every non-zero order in March and April.
