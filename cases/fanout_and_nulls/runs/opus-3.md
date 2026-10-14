claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 43 s | USD 0.170 | 2026-10-14 | project f769fdf+wk6ext | prompt c0748c4eed7e

# Two data defects in the raw tables, with offsetting errors

The gaps are exact, so the cause can be pinned down:

| | stg_payments | orders | processor | orders − stg | processor − stg |
|---|---|---|---|---|---|
| credit_card | 853 | 1706 | 871 | **+853 (exactly 2×)** | 18 |
| bank_transfer | 386 | 386 | 411 | 0 | 25 |
| coupon | 169 | 169 | 185 | 0 | 16 |
| gift_card | 199 | 199 | 205 | 0 | 6 |
| **total** | **1607** | **2460** | **1672** | +853 | 65 |

`region_revenue` and `customers` both read `stg_payments` directly and show 1607. So they don't fan out, but they are missing the same 65.00.

## 1. Root cause

**Defect A: `raw_payment_methods` has two rows for `credit_card`.** This causes the +853.

- In `models/orders.sql`, the `order_payments` CTE left-joins `payments` to `payment_methods` on `payment_method`.
- Every credit card payment matches two rows, so it is summed twice.
- Order 2 shows this exactly: one 20.00 credit card payment becomes 40.00.
- Fees are also wrong: 40 × 0.029 = 1.16. Both rates are being applied to the same 20.00.
- The table probably has two processors for credit cards, which fits the `processor` column.
- Nothing tests that `payment_method` is unique in that seed. Only `not_null` is checked.

**Defect B: 8 rows in `raw_payments` have `amount = NULL`.** This causes the −65.

- The breakdown is 3 credit_card, 3 bank_transfer, 1 coupon and 1 gift_card (`payments − with_amount`).
- The ingestion job loaded these as NULL. `stg_payments.sql` passes them through unchanged.
- `sum()` skips NULLs, so these payments disappear without an error.
- The missing amounts match the processor shortfalls: 18 + 25 + 16 + 6 = 65.
- Order 13 is credit_card 5.00 (doubled to 10) plus a NULL bank_transfer. The processor settled 19.00, so payment 15 should be 14.00.
- The `not_null` tests on `orders.*_amount` still pass, because the aggregate is never NULL when at least one payment has a value.

The column is `INTEGER` (cents). A likely cause is that the source file sent some values in a form the loader couldn't cast, such as `14.00` or `1,400`, and it wrote NULL. Check the ingestion job's reject log or the source file to confirm.

## 2. Fix

**A. Make `raw_payment_methods` one row per method.** Finance or the processor contract decides which credit card row is correct. If the processor genuinely changed, model it with valid-from/valid-to dates and join on the payment date too. Until the seed is fixed, `orders.sql` can avoid the fan-out like this:

```sql
payment_methods as (

    select payment_method, max(fee_rate) as fee_rate
    from {{ ref('raw_payment_methods') }}
    group by payment_method

),
```

This is a stopgap only. `max()` picks a rate arbitrarily, so the real fix is in the data.

**B. Re-ingest `raw_payments` so the 8 NULL amounts are populated.** Find them with:

```sql
select * from raw_payments where amount is null;
```

From the reconciliation, three are already known: payment 15 = 1400, payment 30 = 1600 and payment 67 = 600 (cents). The other credit card NULLs total 1800 and the remaining bank transfers total 1100.

Do **not** `coalesce(amount, 0)` in staging. That would hide the problem permanently.

**Then rebuild with a full refresh.** `daily_revenue` is incremental and only rebuilds the last 3 days, so its older rows still hold inflated revenue:

```
dbt build --full-refresh -s daily_revenue+  # or simply: dbt build --full-refresh
```

## 3. Tests that would have caught it

In `models/schema.yml`, add to the `raw_payment_methods` seed:

```yaml
      - name: payment_method
        tests:
          - not_null
          - unique
```

In `models/staging/schema.yml`, add to `stg_payments`:

```yaml
      - name: amount
        tests:
          - not_null
      - name: payment_method
        tests:
          - relationships:
              arguments:
                to: ref('raw_payment_methods')
                field: payment_method
```

Also add a reconciliation test. It catches any fan-out or dropped rows between payments and orders, per method and in total. Save it as `tests/assert_orders_reconcile_to_payments.sql`:

```sql
with orders as (

    select
        round(sum(credit_card_amount), 2) as credit_card,
        round(sum(bank_transfer_amount), 2) as bank_transfer,
        round(sum(coupon_amount), 2) as coupon,
        round(sum(gift_card_amount), 2) as gift_card,
        round(sum(amount), 2) as amount
    from {{ ref('orders') }}

),

payments as (

    select
        round(sum(case when payment_method = 'credit_card' then amount else 0 end), 2) as credit_card,
        round(sum(case when payment_method = 'bank_transfer' then amount else 0 end), 2) as bank_transfer,
        round(sum(case when payment_method = 'coupon' then amount else 0 end), 2) as coupon,
        round(sum(case when payment_method = 'gift_card' then amount else 0 end), 2) as gift_card,
        round(sum(amount), 2) as amount
    from {{ ref('stg_payments') }}

)

select *
from orders, payments
where orders.amount is distinct from payments.amount
   or orders.credit_card is distinct from payments.credit_card
   or orders.bank_transfer is distinct from payments.bank_transfer
   or orders.coupon is distinct from payments.coupon
   or orders.gift_card is distinct from payments.gift_card
```

What each test catches:

- **`unique` on `raw_payment_methods.payment_method`:** fails on Defect A.
- **Reconciliation test:** fails on Defect A, with a credit card gap of 1706 vs 853.
- **`not_null` on `stg_payments.amount`:** fails on Defect B (8 rows).

None of these catch a value that is wrong but not NULL. For that you need a source-level row count and sum check against the processor settlement file, if the ingestion job can load it.
