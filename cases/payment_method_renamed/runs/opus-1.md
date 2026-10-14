claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 22 s | USD 0.117 | 2026-10-14 | project f769fdf | prompt 0206530f44fe

# Diagnosis: March and April payment mix shows 0.00

## 1. Root cause

The problem is **schema drift in `raw_payments`**. The ingestion job added a new column, `method`, which you can see in the `information_schema` output. From March 2018 on, the payment method is written to `method`, and `payment_method` is left **NULL**.

`models/staging/stg_payments.sql` still reads only `payment_method`. That produces two effects:

- **The total is still right.** In `models/orders.sql`, `sum(amount)` doesn't filter on method, so `amount` still adds up (622.00 for March).
- **Every method column is zero.** Each `case when payment_method = '...' then amount else 0 end` sees `NULL = 'credit_card'`, which is not true, so it falls to `else 0`. All four method columns become 0.00. April is affected the same way (139.00 total, all methods 0).

No test failed, for three reasons:

- `accepted_values` on `stg_payments.payment_method` **ignores NULLs**, so the NULL rows pass.
- There is no `not_null` test on `stg_payments.payment_method`.
- The `not_null` tests on `orders.*_amount` can never fail, because `else 0` turns a missing method into 0 instead of NULL.

I haven't seen the raw data, so this conclusion comes from the schema plus the symptoms. Run this to confirm it:

```sql
select date_trunc('month', o.ordered_at) as month,
       p.payment_method, p.method,
       count(*) as payments, sum(p.amount) / 100 as amount
from raw_payments p
join raw_orders o on o.id = p.order_id
group by all
order by 1, 2, 3;
```

What you should see:
- March and April rows have `payment_method` NULL and a value in `method`.
- The card rows for March sum to **334.00**, which matches the processor's statement.

Also check the values that appear in `method`. If they use a different vocabulary (for example `card` or `Credit Card`), they need to be mapped, not just coalesced.

## 2. Fix

Update `models/staging/stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the ingestion job moved the method to `method` from 2018-03; older rows still use `payment_method`
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

If `method` uses different labels, wrap the expression in a `case` that maps them to `credit_card` / `coupon` / `bank_transfer` / `gift_card`.

`orders` is a table, so it is fully rebuilt on the next run. `daily_revenue` only stores `orders` and `revenue`, so it isn't affected. Then run `dbt build`.

It's also worth raising this with the ingestion team. Two populated columns for one attribute is a contract problem on their side.

## 3. Tests that would have caught this

First, in `models/staging/schema.yml`, add `not_null` so a missing method fails at staging:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

Second, add a reconciliation test, `tests/assert_orders_payment_mix_sums_to_amount.sql`. It checks that the method columns add up to the total, which catches any payment that falls through the `case` for whatever reason:

```sql
-- every payment must be attributed to exactly one method
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where abs(coalesce(amount, 0)
          - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
```

Against the current data, this returns orders 57 onward, so the build fails.

## Unrelated issue

`models/schema.yml` documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`. This isn't related to the bug, but the docs are wrong.
