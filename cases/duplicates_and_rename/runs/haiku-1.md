claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 27 s | USD 0.005 | 2026-10-14 | project 8ee5734 | prompt 2e932ff37d63

## 1. Root cause

The ingestion job has changed where payment methods land. `raw_payments` now has two method columns, `payment_method` and `method`. For payments from March 2018 onward, `payment_method` is NULL and the value is in `method`. Orders 57 to 60 and payment ids 65 to 69 and 114 all have NULL `payment_method`. Payment 63 (order 55, Feb 27) still has `credit_card`, which is where the switch happens.

`jaffle_shop/models/staging/stg_payments.sql` reads only `payment_method`, so it passes the NULLs through. The model is doing what it was written to do; the input changed shape under it.

Downstream, `jaffle_shop/models/orders.sql` does `sum(case when payments.payment_method = '...' then amount else 0 end)`. A NULL never equals a method, so every `*_amount` column becomes 0 for those orders. `amount` is still correct because it is a plain `sum(payments.amount)`, which is why the totals are right and the breakdown is zero. The `fees` column is also understated for March and April, because the seed join on `payment_method` finds no fee rate and `coalesce(..., 0)` returns 0.

**Why the tests pass:**
- `accepted_values` on `stg_payments.payment_method` ignores NULLs (the check is `value not in (...)`, which is NULL and filtered out). There is no `not_null` on that column.
- `not_null` on `orders.*_amount` passes, because the CASE returns 0, not NULL.
- Nothing checks that the per-method amounts add up to `amount`.

**Files involved:**
- `models/staging/stg_payments.sql`: reads only `payment_method`. This is where the break surfaces.
- `models/staging/schema.yml`: no `not_null` on `payment_method`.
- `raw_payments` (ingestion output): `payment_method` NULL for March onward, `method` populated.

I have not seen the values in `method`. The fix below assumes they are the same four strings.

## 2. Fix

Check the `method` values first:

```sql
select payment_method, method, count(*) from raw_payments group by 1, 2 order by 1, 2;
```

If `method` uses the four expected values (`credit_card`, `coupon`, `bank_transfer`, `gift_card`), change the staging model:

```sql
-- models/staging/stg_payments.sql
select
    id as payment_id,
    order_id,
    coalesce(payment_method, method) as payment_method,

    -- `amount` is currently stored in cents, so we convert it to dollars
    amount / 100 as amount

from source
```

If `method` uses different spellings, map them in this model before the coalesce.

Then rebuild. `stg_payments` is a view, and `orders` is a table that depends on it:

```
dbt build --select stg_payments+
```

`daily_revenue` and `customers` use only `amount`, so they do not change. No backfill is needed beyond the rebuild.

The ingestion job should also be fixed so it writes one column consistently. The staging change repairs the output, but the raw data is still inconsistent.

## 3. Test that would have caught it

Add a `not_null` to the staging column, and a reconciliation test on `orders`.

```yaml
# models/staging/schema.yml
  - name: stg_payments
    columns:
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

```sql
-- tests/orders_payment_methods_sum_to_amount.sql
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as methods_total
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

The `not_null` fails on the 7 NULL rows in `stg_payments`. The singular test fails on orders 57 to 60 (amount 708 across March, methods total 0). Either one would have stopped the nightly build from reporting success.
