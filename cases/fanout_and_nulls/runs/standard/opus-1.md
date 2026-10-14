claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 41 s | USD 0.204 | 2026-10-14 | project ad0b16b | prompt 1db8a43ee8e9

# Root cause: two data defects in the raw tables, and no test checks either one

The numbers reconcile exactly once you separate the two causes:

| | Processor | `orders` | Explanation |
|---|---|---|---|
| credit_card | 871 | 1706 | 2 × (871 − 18 NULL) = 1706 |
| bank_transfer | | −25 | NULL amounts |
| coupon | | −16 | NULL amounts |
| gift_card | | −6 | NULL amounts |
| **total** | **1672** | **2460** | 1672 − 65 (NULLs) + 853 (credit card counted twice) = 2460 |

`region_revenue` and `customers` show 1607. That is 1672 − 65: they have the NULL loss but not the duplication, because they never join `raw_payment_methods`.

## 1. Root cause

**a) `raw_payment_methods` has two rows for `credit_card`** (`stripe` and `adyen`, both with `fee_rate` 0.029). In `models/orders.sql`, the `order_payments` CTE joins `payments` to `payment_methods` on `payment_method`, so every credit card payment appears twice before the `sum`. This doubles `credit_card_amount`, `amount` and the credit card part of `fees`. Example: order 2 was charged 20.00 and shows 40.00.

**b) `raw_payments.amount` is NULL on some rows** (for example ids 30 and 55 in the sample). The NULLs add up to 65.00: credit card 18, bank transfer 25, coupon 16, gift card 6. `sum()` skips them silently.

Order 13 shows both problems: it settled for 19.00, its 5.00 credit card payment is doubled to 10.00, and the other 14.00 has a NULL amount.

**Why every test passed:**
- Nothing tests `stg_payments.amount`.
- `raw_payment_methods.payment_method` has only `not_null`, no `unique`.
- The `not_null` tests on `orders.*_amount` can never fail, because `sum(case ... else 0 end)` turns NULLs into 0.

## 2. Fix

**Data (NULL amounts):** dbt can't recover these values. Find the affected rows with `select * from raw_payments where amount is null`, then have the ingestion job reload them, or backfill them from the processor statement. Also find out why the loader wrote NULLs, since it may be a parse failure on some amount format.

**Code (`models/orders.sql`):** make the lookup one row per payment method before the join:

```sql
payment_methods as (

    {#-
    Processor and fee rate per payment method, loaded as a seed like the other raw tables.
    A method can have several processors (credit_card: stripe + adyen), so collapse to one
    row per method before joining to payments
    #}
    select
        payment_method,
        max(fee_rate) as fee_rate

    from {{ ref('raw_payment_methods') }}

    group by payment_method

),
```

Using `max` is only safe while all processors for a method charge the same rate (the test below enforces that). If the rates ever differ, `raw_payments` needs a `processor` column so the join can be done on it.

**Then run** `dbt build --full-refresh -s daily_revenue+`. `daily_revenue` is incremental and rebuilds only the last 3 days, so its history is stale: Jan–Mar sum to 1533 there versus 2297 in `orders`. Only April matches.

## 3. Tests that would have caught this

In `models/staging/schema.yml`:

```yaml
  - name: stg_payments
    columns:
      - name: amount
        tests:
          - not_null
```

In `models/schema.yml`, under the `raw_payment_methods` seed:

```yaml
      - name: payment_method
        tests:
          - not_null
          - unique
```

A reconciliation test, `tests/assert_orders_amount_reconciles_to_payments.sql`. This catches fan-outs from any future join:

```sql
-- orders must carry exactly the money in payments, no more and no less
with orders_total as (
    select coalesce(sum(amount), 0) as total from {{ ref('orders') }}
),

payments_total as (
    select coalesce(sum(amount), 0) as total from {{ ref('stg_payments') }}
)

select *
from orders_total, payments_total
where abs(orders_total.total - payments_total.total) > 0.005
```

With the fixed `orders.sql`, the reconciliation test passes even while the NULLs remain, because both sides drop them the same way. Only the `not_null` test on `stg_payments.amount` catches the 65.00 gap, and it will keep failing until `raw_payments` is reloaded.

Finally, a small unrelated mismatch: `schema.yml` documents a `customers.total_order_amount` column, but the model outputs `customer_lifetime_value`.
