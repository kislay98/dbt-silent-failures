claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 26 s | USD 0.177 | 2026-10-14 | project ad0b16b | prompt a85110595fb3

## 1. Root cause

**The `raw_payments` table changed shape upstream, and `models/staging/stg_payments.sql` still reads the old column.**

The ingestion job added a new column, `method`, to `raw_payments`. For newer payments it fills only `method` and leaves `payment_method` NULL:

| id | order_id | payment_method | method |
|---|---|---|---|
| 12 | 11 | credit_card | credit_card |
| 28 | 25 | bank_transfer | bank_transfer |
| 90 | 79 | **NULL** | credit_card |
| 93 | 81 | **NULL** | coupon |
| 114 | 59 | **NULL** | gift_card |

The NULLs start around payment id ~65 (order 57, 2018-03-01) and cover everything after. That is why January and February are right and March and April are not.

`stg_payments` passes the NULL `payment_method` through. In `models/orders.sql`, each `case when payments.payment_method = '...' ... else 0 end` then falls to `0`. The left join to `raw_payment_methods` also misses, so `coalesce(fee_rate, 0)` makes fees 0. `sum(amount)` doesn't depend on the method, so `amount` stays correct (708 / 146). That gives exactly the symptom: correct totals with 0.00 in every method column.

**Why every test passed:**
- `accepted_values` on `stg_payments.payment_method` ignores NULLs by design, so NULLs pass.
- The `not_null` tests on `orders.*_amount` and `fees` can never fail, because the `else 0` / `coalesce(..., 0)` replaces NULLs with zeros.
- Nothing checks that the method columns add up to `amount`.

## 2. Fix

In `models/staging/stg_payments.sql`, read whichever column is populated, and list the columns explicitly:

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_payments') }}

),

renamed as (

    select
        id as payment_id,
        order_id,
        -- the feed moved payment method from `payment_method` to `method` in March 2018;
        -- older rows only have `payment_method`
        coalesce(method, payment_method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)

select * from renamed
```

In the sample, rows that have both columns agree. Before relying on `coalesce`, confirm this across the whole table:

```sql
select count(*) from raw_payments
where payment_method is not null and method is not null and payment_method <> method;  -- expect 0
```

Also ask the ingestion team whether `payment_method` is deprecated. If it is, plan to switch to `method` only.

After `dbt build`, check the result: March `credit_card` should come to 334.00 (the processor statement), and the four method columns should sum to 708.00 for March and 146.00 for April.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add a `not_null` test. This is the key gap, because `accepted_values` lets NULLs through:

```yaml
  - name: stg_payments
    columns:
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
          - relationships:
              arguments:
                to: ref('raw_payment_methods')
                field: payment_method
```

Add a reconciliation test as a singular test, `tests/assert_orders_payment_mix_reconciles.sql`. This catches any payment that isn't attributed to a method, whatever the cause:

```sql
select order_id, amount,
       credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as method_total
from {{ ref('orders') }}
where abs(coalesce(amount, 0)
          - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
```

Optionally, add a model contract (`contract: enforced: true`) or a source column test on `raw_payments`. That flags schema drift like the new `method` column when it happens.

## Separate issue: `daily_revenue` is stale

`daily_revenue` shows March 622 and April 139, but `orders` shows 708 and 146. The model is incremental and rebuilds only the last 3 days. Payments for older dates that arrived later (or were backfilled) never get picked up.

Fix the current data with `dbt build -s daily_revenue --full-refresh`. Then either widen the lookback window or add a test that compares `daily_revenue` monthly totals with `orders`.
