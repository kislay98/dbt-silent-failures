claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 28 s | USD 0.137 | 2026-10-14 | project 8ee5734 | prompt 2e932ff37d63

## 1. Root cause

**The ingestion job changed the shape of `raw_payments`, and `models/staging/stg_payments.sql` still reads the old column.**

- The warehouse schema shows `raw_payments` now has **two** method columns: the original `payment_method` and a new `method`.
- From March 2018 onward, the feed writes the method into `method` and leaves `payment_method` NULL. Every `stg_payments` row for orders 57+ has `payment_method = NULL`, while `amount` is still populated.
- `stg_payments.sql` selects only `payment_method`, so NULL flows through.
- In `models/orders.sql`, each `sum(case when payments.payment_method = '...' then amount else 0 end)` falls to `else 0` for NULL methods. `sum(payments.amount)` still counts them. That is why March and April show totals (708.00, 146.00) with 0.00 in every method column.
- The same NULL breaks the join to `raw_payment_methods`, so `coalesce(fee_rate, 0)` makes **`fees` = 0 for March and April too**. Finance will see that error next.

**Why every test passed:**
- `accepted_values` on `stg_payments.payment_method` ignores NULLs. Its SQL is `where payment_method not in (...)`, and `NULL not in (...)` is not true, so no rows fail.
- The `not_null` tests on `orders.*_amount` can't fail, because the `case ... else 0` turns missing methods into 0.
- No test checks that the method columns add up to `amount`.

`daily_revenue`, `region_revenue` and `customers` use only `amount`, so they are unaffected.

## 2. Fix

`models/staging/stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the feed moved the method from `payment_method` to `method` from March 2018
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

Before shipping, check that `method` uses the same vocabulary:

```sql
select payment_method, method, count(*), sum(amount) / 100 as amount
from raw_payments group by all order by all;
```

If `method` holds different values (e.g. `'Credit Card'`, `'card'`), map them in the same expression, e.g. with a `case`. The strengthened `accepted_values` test below will catch any value that doesn't map. Then run `dbt build` (orders is a table, so a normal rebuild is enough) and confirm March `credit_card` matches the processor's 334.00.

Also tell the ingestion team. Either the rename was intentional, and `payment_method` should eventually be dropped, or it's a bug on their side.

**Also worth checking:** order 59 has two identical 28.00 payments (ids 68 and 114). The ids from 114 up look like late or extra rows. If the processor statement shows only one, it's a duplicate that inflates March. That's a separate issue from the zeros, but worth reconciling while you have the statement open.

## 3. Tests that would have caught it

**a) Make the method mandatory** in `models/staging/schema.yml`. This is the direct catch, since it fails on the first NULL:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

**b) Reconcile the method breakdown to the total.** This catches any future method that falls through the pivot. Add `tests/assert_order_payment_methods_sum_to_amount.sql`:

```sql
-- every order's per-method amounts must add up to its total amount
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where abs(
    coalesce(amount, 0)
    - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)
) > 0.005
```

Today this would return orders 57 onward and fail the build. Optionally, add a `relationships` test from `stg_payments.payment_method` to `ref('raw_payment_methods')` so an unknown method can't silently get a 0 fee rate.
