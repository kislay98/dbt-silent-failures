claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 29 s | USD 0.180 | 2026-10-14 | project ad0b16b | prompt a85110595fb3

## 1. Root cause

**The data:** `raw_payments` has a new column, `method`. The payment method for newer payments now lands in `method`, and the old `payment_method` column is `NULL` for those rows:

```
id | order_id | payment_method | amount | method
28 |       25 | bank_transfer  |   2000 | bank_transfer   ← old rows: both populated
90 |       79 | NULL           |   1800 | credit_card     ← new rows (≈ March onward): only `method`
115|       68 | NULL           |   1600 | credit_card
```

This looks like the source or ingestion job renamed `payment_method` to `method`. The loader added the new column and left the old one null for new data. Older rows still have both columns filled.

**The code:** `jaffle_shop/models/staging/stg_payments.sql` still selects only `payment_method`. So every March/April payment reaches `orders.sql` with `payment_method = NULL`, and the effects follow from that:

- Every `case when payments.payment_method = 'credit_card' …` evaluates to false, so all four `*_amount` columns are `0`.
- `sum(payments.amount)` ignores the method, so `amount` is still correct (708.00 and 146.00).
- The `left join payment_methods` finds no match, and `coalesce(fee_rate, 0)` turns that into `fees = 0.00`. Processor fees for March and April are understated too, not only the payment mix.

**Why every test passed:**
- `accepted_values` on `stg_payments.payment_method` ignores NULLs.
- The `not_null` tests on `orders.*_amount` can't fire, because `sum(case … else 0 end)` turns a missing method into `0`, not `NULL`.

## 2. Fix

In `jaffle_shop/models/staging/stg_payments.sql`, read the new column and fall back to the old one for historical rows:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        coalesce(method, payment_method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

Then rebuild everything downstream: `dbt build --select stg_payments+`.

To verify the fix, check that March and April now split fully across the four methods (the methods should sum to `amount`). March `credit_card` should reconcile with the processor's 334.00:

```sql
select date_trunc('month', order_date) m, sum(credit_card_amount) cc, sum(amount) amt,
       sum(credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount) by_method
from orders group by 1 order by 1;
```

It's also worth confirming with whoever owns ingestion that `method` is the column going forward, and whether `payment_method` will be dropped.

## 3. Tests that would have caught it

Add `not_null` next to `accepted_values` in `models/staging/schema.yml`, because `accepted_values` doesn't check for nulls:

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

Also add a reconciliation test on `orders`, because any unmapped or new method would break this invariant. For example, `tests/assert_orders_payment_methods_sum_to_amount.sql`:

```sql
-- every payment must be attributed to exactly one known method
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as by_method
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
```

Optionally, add a `relationships` test from `stg_payments.payment_method` to `raw_payment_methods.payment_method`. That would also catch payments that silently get a 0 fee rate.

## Separate issue: `daily_revenue`

`daily_revenue` disagrees with `orders`:

| Month | `daily_revenue` | `orders` |
|---|---|---|
| March | 622 | 708 |
| April | 139 | 146 |
| All months | 1672 | 1765 |

All other models total 1765. The incremental filter only rebuilds the last 3 days, so payments that arrive later for older order dates are never picked up. This is not the cause of the payment-mix problem. To fix it:

- Run `dbt build --select daily_revenue --full-refresh` now.
- Then either widen the lookback, or base it on payment load time instead of `order_date`.
- Add a singular test that compares `sum(revenue)` with `sum(orders.amount)`.
