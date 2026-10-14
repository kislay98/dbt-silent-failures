claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 48 s | USD 0.008 | 2026-10-15 | project ad0b16b | prompt 037c69d41959

## 1. Root cause

**`jaffle_shop/models/orders.sql`, joined to the seed `raw_payment_methods`, which has two `bank_transfer` rows.**

The `raw_payment_methods` table (5 rows) has `bank_transfer` twice, once for `anz` and once for `westpac`, both at fee rate 0.005. That makes its grain `(payment_method, processor)`, but `orders.sql` joins it on `payment_method` alone:

```sql
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
```

Every `bank_transfer` payment is duplicated before the `group by order_id`, so the sums double:

- `bank_transfer_amount` is doubled: 822 = 2 × 411.
- `amount` (`sum(payments.amount)`) is doubled for bank-transfer orders too: 2083 = 1672 + 411.
- `fees` is doubled for those payments.

The sample numbers match this exactly:
- Order 5: 17 paid, 34 shown.
- Order 20: fee 0.15 = 2 × (15 × 0.005), so the real payment is 15 and the shown amount is 30.

Credit card, coupon and gift card are unaffected, and `customers` and `region_revenue` never touch `raw_payment_methods`, which is why they still total 1672.

**Why the tests pass:** the fan-out happens before the `group by order_id`, which collapses the duplicate rows back to one row per order. So `unique` and `not_null` on `orders.order_id` pass, and the inflated values are never compared against the source payments. The schema tests on `raw_payment_methods` only check `not_null`, not uniqueness, and the `accepted_values` test only covers `stg_payments`. The `raw_payment_methods` description ("One row per payment method") is also contradicted by the data.

**Two related points:**
- The log shows no seed step, and you said the raw tables come from the ingestion job. So the 5-row `raw_payment_methods` in the warehouse may come from the loader, not from the CSV in the repo. Check the CSV and the loader; fixing the CSV alone may not change the warehouse.
- `daily_revenue` totals 1,696, which is between the correct 1,672 and the buggy 2,083. That fits the incremental model: only the last three days are rebuilt, so older rows still carry values from before the bad seed data and are never corrected.

## 2. Fix

**a) Make the model robust to the seed grain** (`orders.sql`). This takes effect on the next build regardless of what the loader does:

```sql
payment_methods as (

    {#-
    One row per payment method. The processor feed can list a method more than once
    (bank_transfer has anz and westpac); joining on the method alone would double-count payments
    #}
    select payment_method, max(fee_rate) as fee_rate
    from {{ ref('raw_payment_methods') }}
    group by payment_method

),
```

Both `bank_transfer` rates are 0.005, so fees don't change either way. Which processor actually settles bank transfers is a business question for finance. If the rates ever differ, `max` will hide that, which is why the uniqueness test in (b) matters.

**b) Fix the source data.** Remove the duplicate row from the seed CSV, or fix the loader to emit one row per method, and confirm which processor is correct.

**c) Rebuild, including history.** Because `daily_revenue` is incremental and only rebuilds three days, it needs a full refresh:

```
dbt build --select orders+ --full-refresh
```

Check afterwards: `amount` should total 1672 and `bank_transfer_amount` should total 411 for January to April.

## 3. Tests that would have caught it

**Reconcile against the source payments, not against the method columns.** The method columns and `amount` are doubled together, so comparing `amount` with `sum(credit_card_amount + ...)` would still pass. Compare with `stg_payments` instead. Save as `jaffle_shop/tests/orders_amount_matches_payments.sql`:

```sql
select o.order_id, o.amount, p.paid
from {{ ref('orders') }} o
join (
    select order_id, sum(amount) as paid
    from {{ ref('stg_payments') }}
    group by order_id
) p on o.order_id = p.order_id
where abs(o.amount - p.paid) > 0.005
```

**Enforce the seed's documented grain** in `models/schema.yml`:

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - unique
          - not_null
```

This will fail on the current data until the duplicate is removed, which is the point.

I haven't run any of this; the conclusions come from the log, schemas and sample outputs. To confirm the duplicate, run `select payment_method, count(*) from raw_payment_methods group by 1 having count(*) > 1`, which should return `bank_transfer` with 2.
