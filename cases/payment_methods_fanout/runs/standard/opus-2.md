claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 38 s | USD 0.199 | 2026-10-15 | project ad0b16b | prompt 037c69d41959

# Diagnosis: duplicate `bank_transfer` row in `raw_payment_methods` fans out the payments join

## 1. Root cause

**Data:** `raw_payment_methods` has two rows for `bank_transfer`, one per processor. It is supposed to have one row per payment method.

```
bank_transfer │ anz     │ 0.005
bank_transfer │ westpac │ 0.005
```

**Code:** `jaffle_shop/models/orders.sql` left-joins `stg_payments` to this table on `payment_method` before aggregating, in the `order_payments` CTE. Every bank-transfer payment matches two rows, so it is counted twice in `bank_transfer_amount`, `total_amount` and `fees`.

The figures match this exactly:

| | Correct | Doubled |
|---|---|---|
| Bank transfer | 411.00 | 822.00 |
| Total | 1672.00 | 1672 + 411 = **2083.00** |
| Order 5 | 17.00 | 34.00 |
| Order 90 (payment 103 = 200 cents) | 2.00 | 4.00 |

- The other three methods have one row each, so they reconcile.
- The bank-transfer fees are doubled too.
- `customers` and `region_revenue` read `stg_payments` directly and never join this table, so they still total 1672.00.

**Side effect on `daily_revenue`:** it totals 1696.00. January to March are correct (496 / 415 / 622) because those days were built before the duplicate row arrived. April shows 163, against 139 correct and 178 doubled. The 3-day incremental window rebuilt only the last few days from the bad `orders`, so `daily_revenue` is partly contaminated.

**Why no test failed:**
- No test checks uniqueness of `raw_payment_methods.payment_method`. The seed is only tested for `not_null`.
- The nightly run did not execute the seed tests at all. The log shows "32 data tests" found but only 28 run, and no seeds.
- Every `orders` test is `not_null` or `unique` on `order_id`. A fan-out that is aggregated back to one row per order passes all of them.

## 2. Fix

**Code fix:** add a staging model that guarantees one row per payment method, and join `orders.sql` to it instead of the raw table.

`models/staging/stg_payment_methods.sql`:
```sql
with source as (

    {#-
    The processor feed can list several processors per payment method,
    so collapse it to one row per payment method
    #}
    select * from {{ ref('raw_payment_methods') }}

),

renamed as (

    select distinct
        payment_method,
        fee_rate

    from source

)

select * from renamed
```

In `models/orders.sql`:
```sql
payment_methods as (

    select * from {{ ref('stg_payment_methods') }}

),
```

I used `select distinct payment_method, fee_rate` rather than `group by payment_method` with `max(fee_rate)` on purpose:
- If the processors ever charge different rates, you get two rows again.
- The `unique` test below will then fail loudly instead of a fee rate being picked silently.

**Data fix (alternative or additional):** if the feed should list each method once, have the ingestion job dedupe it, or remove the extra row. The code fix still protects you against the next time this happens.

**Rebuild:** run `dbt build --full-refresh -s orders+`. The full refresh matters for `daily_revenue`. The days contaminated in earlier runs drift out of the 3-day window and would otherwise never be corrected.

## 3. Tests that would have caught it

**Uniqueness of the join key.** Add this to `models/staging/schema.yml`:
```yaml
  - name: stg_payment_methods
    columns:
      - name: payment_method
        tests:
          - unique
          - not_null
```

Also add `unique` on `raw_payment_methods.payment_method` and `raw_customer_regions.customer_id` in `models/schema.yml`. Then make sure the nightly job actually runs seed and source tests: whatever selector excludes seeds is currently skipping them too.

**Reconciliation test,** which catches any fan-out regardless of cause. Add `tests/assert_order_amounts_match_payments.sql`:
```sql
-- Each order's amount must equal the sum of its payments
with payments as (

    select order_id, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by order_id

)

select
    orders.order_id,
    orders.amount,
    payments.amount as payments_amount

from {{ ref('orders') }} as orders

left join payments
    on orders.order_id = payments.order_id

where coalesce(orders.amount, 0) != coalesce(payments.amount, 0)
   or orders.amount != orders.credit_card_amount + orders.coupon_amount
                     + orders.bank_transfer_amount + orders.gift_card_amount
```

On the current data this fails on every order with a bank transfer, including orders 5, 90, 73 and 20.

## Minor: column name mismatch in `customers`

`models/schema.yml` documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`. It is unrelated to this bug but worth renaming one of them.
