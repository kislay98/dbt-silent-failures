claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 32 s | USD 0.076 | 2026-10-14 | project bef6b69 | prompt aaad08d98757

## 1. Root cause

**Primary: `models/staging/stg_payments.sql` reads a column the ingestion job stopped filling.**

`raw_payments` now has two method columns: `payment_method` and a new `method`. From March onward, the loaded rows have `payment_method = NULL`, and the real value is presumably in `method`. The sample of `stg_payments` shows this: orders 58, 76 and 92 have `payment_method` NULL. January and February rows still use `payment_method`, which is why they are correct.

The failure then flows through the models:
- `orders.sql` computes `sum(case when payment_method = 'credit_card' ...)`. NULL never matches, so every method column is 0, while `sum(amount)` still totals correctly. That is the 0.00 by method against 595.27 in total.

Every test passed for these reasons:
- `accepted_values` on `stg_payments.payment_method` ignores NULLs.
- There is no `not_null` on `payment_method`.
- The `not_null` tests on `orders.*_amount` pass because the `else 0` branch produces 0, not NULL.
- Nothing reconciles the method columns to `amount`, or `amount` to the settlement.

**Secondary: the amounts are also understated (inferred from the figures, please confirm).**

The total is also short of settlement: 622.00 − 595.27 = 26.73 for March, and 139.00 − 106.33 = 32.67 for April. Both gaps are exactly 99% of a whole number: 26.73 / 0.99 = 27.00 and 32.67 / 0.99 = 33.00.

The pattern is consistent with some rows being loaded in whole dollars instead of cents, so the `amount / 100` in `stg_payments.sql` shrinks them by 99%. The tiny amounts (0.02, 0.23, 0.07, 0.24) look like these rows.

- **April:** 33.00 matches the marketing coupon ledger exactly.
- **March:** 27 of the 45.00 ledger total. The other 18 would be the correctly scaled 18.00 on order 58.

So the dollar-scaled rows are most likely the coupon rows. I can't see the seed data, so this is from the numbers only. Confirm with:

```sql
select method, count(*), sum(amount) from raw_payments
where id >= 66 group by 1;  -- compare small-amount rows to the coupon ledger
```

## 2. Fix

**Staging model.** This is safe to apply now:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        coalesce(payment_method, method) as payment_method,
        amount / 100 as amount

    from source

)
```

Then fix the amount scaling. Once the query above confirms which rows are in dollars, either:
- have the ingestion team load those rows in cents (preferred), or
- add a documented rule in `stg_payments`, for example `case when <dollar-scaled rows> then amount else amount / 100.0 end`.

I'm not guessing that rule without seeing the data. Also ask ingestion to stop writing to two columns.

**Rebuild.** `daily_revenue` is incremental and only reprocesses the last three days, so March and April stay stale. Run:

```
dbt build --full-refresh
```

After the rebuild, March and April should total 622.00 and 139.00, with coupons of 45.00 and 33.00.

## 3. Tests that would have caught it

```yaml
# models/staging/schema.yml
      - name: payment_method
        tests:
          - not_null
          - accepted_values: {arguments: {values: [credit_card, coupon, bank_transfer, gift_card]}}
```

```sql
-- tests/assert_order_methods_sum_to_amount.sql
select order_id
from {{ ref('orders') }}
where amount is not null
  and abs(credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount - amount) > 0.001
```

The second test fails for every March and April order with the current code.

Two more tests would help:
- **Schema drift:** a source freshness or schema check on `raw_payments` that alerts when columns change, which would have flagged `method`.
- **Settlement reconciliation:** a monthly comparison of `sum(amount)` to the processor settlement and coupon ledger, which would have caught the scaling problem.
