claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 21 s | USD 0.066 | 2026-10-14 | project 8ee5734 | prompt 2e932ff37d63

## 1. Root cause

The bug is in `models/staging/stg_payments.sql`, triggered by schema drift in `raw_payments`.

- `raw_payments` now has two columns for the same thing: `payment_method` (the original) and a new `method`. The ingestion job appears to have started writing the method into `method` from around payment id 65 (the first March order is 57), leaving `payment_method` NULL.
- `stg_payments` only selects `payment_method`, so every payment from March onward has `payment_method = NULL`. The sample shows this: payments 65-69 and 114 are all NULL.
- In `orders.sql`, every `case when payments.payment_method = '...'` is false for those rows, so the four method columns sum to 0. `amount` is `sum(payments.amount)`, which doesn't depend on the method, so it stays correct (708 and 146).
- Reconciliation: `orders` total (1765.00) equals the `stg_payments` total (1765.00), so no payment is lost, only its method.

Why the tests passed:
- `accepted_values` on `payment_method` ignores NULLs, and there is no `not_null` on that column.
- The `not_null` tests on `orders.*_amount` pass because `sum(case ... else 0 end)` is never NULL.
- Nothing checks that the method columns add up to `amount`.

Side effect: `fees` is also understated for March and April. The join to `raw_payment_methods` finds no match for a NULL method, and `coalesce(fee_rate, 0)` turns that into a fee of 0, which `not_null` also lets through.

One more thing to check. Payment 114 (order 59, 28.00) looks identical to payment 68 (order 59, 28.00), and ids 114-118 are beyond the original range. They may be legitimate or a double load. I can't tell from the sample, so compare them against the processor statement.

## 2. Fix

Run this first to confirm `method` uses the same vocabulary as `payment_method`:
```sql
select method, count(*) from raw_payments where payment_method is null group by 1
```
If it uses different labels such as `card`, map them in the staging model.

Then change `stg_payments.sql`:
```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- ingestion started writing the method to `method` from March; older rows still use `payment_method`
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

Then run `dbt build`. `orders`, `customers` and `region_revenue` are tables, so they rebuild fully. `daily_revenue` only holds `order_date` and `revenue`, which don't change, so it needs no refresh. If you remove a duplicate payment, run it with `--full-refresh`, because the incremental model only rebuilds the last 3 days.

Also ask the ingestion owners to stop writing to two columns, or to tell you which one is canonical.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add `not_null` to `stg_payments.payment_method`. This is the direct catch, since `accepted_values` skips NULLs:
```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Add a singular test, `tests/assert_orders_method_amounts_sum_to_amount.sql`, which catches any future case where the breakdown doesn't reconcile with the total:
```sql
select order_id, amount
from {{ ref('orders') }}
where abs(
    coalesce(credit_card_amount, 0) + coalesce(coupon_amount, 0)
  + coalesce(bank_transfer_amount, 0) + coalesce(gift_card_amount, 0)
  - coalesce(amount, 0)
) > 0.001
```

Optionally, add a test on the raw table that flags this drift at the source, `tests/assert_raw_payments_method_populated.sql`:
```sql
select id from {{ ref('raw_payments') }}
where payment_method is null and method is null
   or payment_method is not null and method is not null
```
