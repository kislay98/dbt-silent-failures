claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 19 s | USD 0.111 | 2026-10-14 | project f769fdf | prompt 0206530f44fe

# Payment mix debug: March and April payments have no `payment_method`

## 1. Root cause

The problem is in the raw data. It shows up through **`models/staging/stg_payments.sql`** and is passed on by **`models/orders.sql`**.

- `raw_payments` now has two method columns: `payment_method` and a new `method` column (last row of the `information_schema` output). The ingestion job appears to have started writing the method into `method` from about 2018-03-01 onward, leaving `payment_method` NULL.
- `stg_payments.sql` only selects `payment_method`, so payments from March onward reach `orders.sql` with a NULL method.
- In `orders.sql`, `case when payment_method = '...' then amount else 0 end` is never true for NULL, so every method bucket gets 0. `sum(amount)` ignores the method, so `amount` is still correct. That is why revenue reconciles with the settlement while the mix shows zeros.
- **April is affected too**: its 139.00 total also shows 0 in every bucket. It is not just March.

**Why every test passed:**
- `accepted_values` on `stg_payments.payment_method` skips NULLs, so NULL methods pass.
- The `not_null` tests on `orders.*_amount` can never fail, because the `else 0` turns missing values into 0.
- No test checks that the method buckets add up to `amount`.

I worked this out from the schema and output you pasted; I haven't queried the data. This confirms it, including how `method` is spelled:

```sql
select date_trunc('month', o.ordered_at) as month,
       p.payment_method, p.method, count(*) as n, sum(p.amount) / 100 as amount
from raw_payments p join raw_orders o on o.id = p.order_id
group by all order by 1, 2, 3;
```

You should see `payment_method` NULL and `method` filled for March and April. The `credit_card` rows for March should come to 334.00, which matches the processor's statement.

## 2. Fix

In `models/staging/stg_payments.sql`, read whichever column is filled:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- ingestion moved the method from `payment_method` to `method` in March 2018
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

If the query above shows that `method` uses different values (for example `card` or `CREDIT_CARD`), map them to the four canonical values with a `case`/`lower()` inside the `coalesce`. The tests below will catch any value you miss.

Then rerun `dbt build`. Separately, ask the ingestion owners whether the column rename was intended, so the two columns can be merged at the source.

## 3. Tests that would have caught it

**a) `not_null` on the method**, in `models/staging/schema.yml`:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

**b) A check that the method buckets add up to the total.** This catches the problem whatever its cause: NULL methods, new method values, or a method missing from the `payment_methods` list. Add `tests/assert_orders_payment_mix_reconciles.sql`:

```sql
-- every payment must land in exactly one payment-method bucket
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where abs(coalesce(amount, 0)
          - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
```

On the current data, this test would have failed for every order from 2018-03-01 onward (orders 57 to 99).
