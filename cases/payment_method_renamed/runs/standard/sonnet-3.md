claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 20 s | USD 0.084 | 2026-10-14 | project ad0b16b | prompt 57278f2875bf

## 1. Root cause

The payments feed changed shape partway through the data, and `stg_payments.sql` only reads the old column.

- `raw_payments` has two method columns: `payment_method` and `method`. The sample rows show the ingestion job started writing the method into `method` and leaving `payment_method` NULL. Earlier rows (e.g. ids 12, 28, 57) have both populated; later ones (ids 74, 90, 93, 97, 111, 112 and others) have `payment_method = NULL` with the real value in `method`.
- `models/staging/stg_payments.sql` selects only `payment_method`, so those rows reach `stg_payments` with a NULL method. Their amounts are still there.
- `models/orders.sql` builds each bucket with `case when payments.payment_method = '...'`, so NULL-method payments fall into no bucket. They still count in `sum(payments.amount)`.
  - **Totals still match:** `amount` is 622.00 for March and matches settlement.
  - **Buckets are empty:** every method column is 0.00 for March, and for April too (139.00 total, all zeros).
- Fees are also 0.00 for March and April. The join to `raw_payment_methods` on a NULL method finds nothing and `coalesce(fee_rate, 0)` hides it. So the fee numbers are wrong too, not just the mix.

**Why every test passed:** `accepted_values` on `stg_payments.payment_method` ignores NULLs. The `not_null` tests on `orders.*_amount` pass because the `case ... else 0` always produces a number. Nothing checked that the buckets add up to `amount`, or that `payment_method` is populated.

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

Then run `dbt build --full-refresh -s stg_payments+`. `orders`, `customers` and `region_revenue` are tables and rebuild anyway. `daily_revenue` doesn't use the method columns, so it isn't affected.

March card volume should then reconcile to the processor's 334.00, and fees for March and April should be non-zero. I couldn't run this against the data, so please confirm those two figures after the rebuild.

Also ask the ingestion owner to either keep populating `payment_method` or document the rename, and decide whether the old rows need backfilling.

## 3. Tests that would have caught it

Staging: make NULL methods a failure, since `accepted_values` skips them. In `models/staging/schema.yml`:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Mart: assert that the buckets sum to the total. Save as `tests/assert_orders_payment_mix_sums_to_amount.sql`:

```sql
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as bucket_total
from {{ ref('orders') }}
where amount is not null
  and abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

Either test alone would have failed on the first NULL-method payment. Optionally, add a `not_null` test for `fee_rate` after the join, or a source freshness/schema check that fails when `raw_payments` gains or changes columns.
