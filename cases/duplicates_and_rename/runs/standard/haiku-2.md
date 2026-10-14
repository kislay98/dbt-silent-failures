claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 39 s | USD 0.007 | 2026-10-14 | project ad0b16b | prompt a85110595fb3

## 1. Root cause

**The ingestion job is writing the payment method to a different column for March and April, and `stg_payments.sql` only reads the original one.**

- `raw_payments` now has two method columns, `payment_method` and `method`. In the sample, the rows where `payment_method` is NULL carry the real value in `method` (`credit_card`, `coupon`, `bank_transfer`, `gift_card`). The NULL rows are concentrated in the March and April loads, which fits the totals. I inferred this from the aggregates and sample rows, not from a full scan of the table.
- `jaffle_shop/models/staging/stg_payments.sql` selects only `payment_method`, so those payments reach `orders` with a NULL method.
- `jaffle_shop/models/orders.sql` then goes wrong in three places:
  - `case when payments.payment_method = 'credit_card' ...` is never true for NULL, so every method column is 0 for those orders.
  - `sum(payments.amount)` still counts the NULL-method payments, so `amount` is correct (708 and 146).
  - The `raw_payment_methods` join finds no `fee_rate` for NULL, so `coalesce(..., 0)` gives zero fees. That is why March and April show `fees = 0.0`.
- The tests pass for three reasons:
  - `accepted_values` ignores NULLs.
  - The `not_null` tests on `credit_card_amount` and the other method columns can't fail, because the `else 0` in the SQL never returns NULL.
  - Nothing tests `stg_payments.payment_method` for NULL.

There is also a second, separate problem in `jaffle_shop/models/daily_revenue.sql`. March revenue there is 622 against 708 in `orders`, and April is 139 against 146. The per-day order counts match, so the amounts are stale. The model is incremental and only rebuilds the last three days, so payments that landed late for older orders were never picked up. This is not the cause of the payment-mix error, but finance will see it in any reconciliation.

Before changing anything, confirm that the two columns never disagree when both are populated:

```sql
select count(*) from raw_payments where payment_method is not null and method <> payment_method;
```

## 2. Fix

**Code fix in `jaffle_shop/models/staging/stg_payments.sql`:**

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        -- the ingestion job fills `method` for newer loads and leaves `payment_method` NULL
        coalesce(payment_method, method) as payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source

)
```

Then:

- **Rebuild the stale table:** `dbt build --select daily_revenue+ --full-refresh`. Without a full refresh, the March and April revenue stays wrong.
- **Expect fees to change:** March and April fees will rise from 0 once the methods are populated. Tell finance this before they see the new numbers.
- **Fix the ingestion job:** the coalesce hides the symptom. Whoever owns the loader should decide which column is canonical and backfill `payment_method` for the affected loads.

I have not run any of this. The checks above are from the artifacts you sent.

## 3. Tests that would have caught it

**a. Make the staging test fail on the NULLs.** In `jaffle_shop/models/staging/schema.yml`, add `not_null` to `stg_payments.payment_method`. This would have failed on the first run after the load changed.

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

**b. A singular test that reconciles method totals to the order amount.** Create `jaffle_shop/tests/orders_methods_sum_to_amount.sql`:

```sql
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as methods_total
from {{ ref('orders') }}
where abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.005
```

This fails on March and April right away, because `amount` is 708 while the methods sum to 0. It also catches any future payment method the accepted-values list doesn't know about.

**c. A singular test that reconciles `daily_revenue` to `orders`.** Create `jaffle_shop/tests/daily_revenue_matches_orders.sql`:

```sql
with d as (select sum(revenue) as total from {{ ref('daily_revenue') }}),
     o as (select sum(amount) as total from {{ ref('orders') }})

select d.total as daily_revenue_total, o.total as orders_total
from d cross join o
where abs(d.total - o.total) > 0.005
```

This would have flagged the stale incremental window. It will fail until the full refresh in section 2 has run.
