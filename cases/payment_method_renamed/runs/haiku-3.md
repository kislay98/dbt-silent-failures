claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 24 s | USD 0.004 | 2026-10-14 | project bef6b69

## 1. Root cause

The March–April break is in the raw payments data, not in the models. Starting on 2018-03-01, `raw_payments.payment_method` is NULL for the affected rows, while the card, coupon, and gift-card values appear to be in the `method` column that the ingestion job added. Nothing in the project reads `method`.

- **`stg_payments.sql`** selects only `payment_method`. Every March payment therefore arrives in staging with a NULL method.
- **`orders.sql`** uses `sum(case when payment_method = 'credit_card' then amount else 0 end)` and the same for each method. A NULL never equals a string, so every method column is 0. The `sum(amount)` into `total_amount` still counts the row, which is why `amount` totals 622.00 while the method columns show 0.
- **Why no test fails:** the `accepted_values` test on `stg_payments.payment_method` ignores NULLs. It only flags non-null values outside the list. There is no `not_null` on `payment_method`. The `not_null` tests on `orders.*_amount` can't fail either, because the `else 0` always produces a non-null value.
- **Why the month boundary matters:** Feb 28 is clean and Mar 1 is fully broken, so the cause is a change in the feed around 2018-03-01, not random rows. The break continues through April.

The `raw_payments` schema backs this up: it has both `payment_method` and `method`, which suggests a rename or a second column was introduced.

This is inferred from the output, since I haven't seen the raw rows. Confirm it before fixing:

```sql
select payment_method is null as pm_is_null, payment_method, method, count(*) as n, sum(amount) / 100.0 as dollars
from raw_payments
where id in (select id from raw_payments) -- all rows
group by 1, 2, 3
order by 1, 2, 3;
```

If the NULL rows have `method` values of `credit_card`, `coupon`, `bank_transfer`, or `gift_card` and the March card total is 334.00, the diagnosis is confirmed. If `method` also has a different spelling or casing, map it explicitly.

**Secondary risk, not causing this bug:** `region_revenue` joins `raw_customer_regions`, which has an `updated_at` column and no `unique` test on `customer_id`. Duplicate customer rows there would double-count revenue.

## 2. Fix

**Stopgap in staging** (`models/staging/stg_payments.sql`), so the models get a method until the ingestion job is corrected:

```sql
        coalesce(payment_method, method) as payment_method,
```

Only do this after the verification query shows `method` holds the right values. Add a `not_null` test so a future gap fails loudly:

```yaml
      - name: payment_method
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['credit_card', 'coupon', 'bank_transfer', 'gift_card']
```

**Proper fix:** correct the ingestion job so it writes `payment_method`, drop or stop writing `method`, and backfill the March and April rows in `raw_payments`. The `method` column should not survive in the raw table alongside `payment_method`.

**Rebuild:** run `dbt build --select orders+`. `daily_revenue` is incremental and only rebuilds the last three days, so run `dbt build --select daily_revenue --full-refresh` if you want the history rebuilt. Its revenue totals were not affected by this bug, since they come from `amount`.

## 3. A test that would have caught this

Add a singular test that the four method columns reconcile to the order amount. It fails on every March and April row right now:

`tests/assert_orders_payment_methods_reconcile.sql`:

```sql
select
    order_id,
    amount,
    credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount as methods_total
from {{ ref('orders') }}
where amount is not null
  and abs(amount - (credit_card_amount + coupon_amount + bank_transfer_amount + gift_card_amount)) > 0.001
```

Pair it with the `not_null` on `stg_payments.payment_method` above. The `not_null` would have flagged the NULLs in the staging layer. The reconciliation test would have flagged the wrong attribution in `orders` even if a new, unexpected value appeared.
