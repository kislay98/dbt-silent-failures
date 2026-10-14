claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 27 s | USD 0.178 | 2026-10-14 | project ad0b16b | prompt a85110595fb3

## 1. Root cause

**The ingestion job changed the `raw_payments` schema partway through the data, and `models/staging/stg_payments.sql` still reads the old column.**

- `raw_payments` now has two columns for the payment method: the original `payment_method` and a new `method`. Older rows (e.g. ids 12 and 28) have both filled in. Newer rows, which are the payments for the March and April orders (ids 65, 81, 86, 90, 93, 97, 111–115 in the sample), have `payment_method = NULL` and the value only in `method`.
- `stg_payments.sql` selects only `payment_method`, so those payments reach `stg_payments` with a NULL method. You can see this in the `stg_payments` sample (payment ids 65, 81 and 86).
- In `models/orders.sql`:
  - Every `case when payments.payment_method = '<method>'` evaluates to false for those rows, so each `*_amount` column becomes `0`.
  - `sum(payments.amount)` still counts them. That is why `amount` shows 708.00 and 146.00 while every per-method column shows 0.00.
  - The `left join raw_payment_methods` finds no match, and `coalesce(fee_rate, 0)` turns that into 0, so `fees` is also 0.00 for March and April.

**Why every test passed:**
- `accepted_values` on `stg_payments.payment_method` ignores NULLs.
- Nothing checks `payment_method` for NULLs.
- The `not_null` tests on `orders.*_amount` can't catch this, because the `else 0` in the CASE turns a missing method into a real zero.

## 2. Fix

In `models/staging/stg_payments.sql`, read the method from whichever column the ingestion job filled:

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

Then rebuild the affected models:

```
dbt build -s stg_payments+ --full-refresh
```

The `--full-refresh` is needed so the incremental `daily_revenue` is rebuilt as well.

After the rebuild, March's `credit_card` total should match the processor's 334.00, and `fees` will be populated. Also confirm with the ingestion owners whether `method` is now the canonical column. If it is, the long-term version is `coalesce(method, payment_method)`, or a backfill of the raw table, with the old column dropped once it's empty.

## 3. Tests that would have caught it

Add `not_null` to the payment method in `models/staging/schema.yml`. This would have failed on the first night:

```yaml
  - name: stg_payments
    columns:
      ...
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Also add a reconciliation test, `tests/assert_order_payment_methods_sum_to_amount.sql`. It guards `orders.sql` against any payment that falls outside the hard-coded method list, whether NULL or a new method:

```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

A `relationships` test from `stg_payments.payment_method` to `raw_payment_methods.payment_method` would also help, so the fee join can't silently fall back to 0.

## A separate issue to check

`daily_revenue` does not match `orders`: March is 622 vs 708, and April is 139 vs 146. Its total is 1672, versus 1765 in `orders`, `customers` and `region_revenue`.

This has nothing to do with the column change. It is the incremental window: payments that land more than 3 days after `max(order_date)` are never re-aggregated into older dates. The full refresh above will correct the numbers for now. A lasting fix needs a wider lookback, or filtering on payment arrival rather than `order_date`.
