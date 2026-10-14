claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 33 s | USD 0.104 | 2026-10-14 | project ad0b16b | prompt 1db8a43ee8e9

## 1. Root cause

There are two independent faults. Together they explain the 2460.00 vs 1672.00 gap exactly.

**A. `raw_payment_methods` has two rows for `credit_card` (stripe and adyen), and `models/orders.sql` joins on `payment_method` alone.**

- `left join payment_methods on payments.payment_method = payment_methods.payment_method` returns two rows for every credit card payment.
- `credit_card_amount`, `total_amount` (exposed as `amount`) and `fees` are summed after that join, so credit card money and fees are counted twice.
- Order 2 is a 20.00 card charge shown as 40.00.
- Nothing else is affected, because `region_revenue` and `customers` don't use this join.
- `unique(order_id)` still passes, because the model groups by `order_id` after the fan-out.

**B. `raw_payments.amount` is NULL for some rows (for example ids 30 and 55).**

- `stg_payments` passes the NULLs through, `sum()` skips them, and the `not_null` tests sit on `orders`, where sums of other non-null rows hide the gap.
- That explains the bank transfer, coupon and gift card shortfalls of 25, 16 and 6.
- Order 13 settled for 19.00 but shows 10.00: 5.00 of card money doubled, plus a NULL-amount payment worth about 14.00 that is missing.

**The numbers reconcile:**

- Correct totals less the NULL amounts give 1672 − (25 + 16 + 6 + 18) = 1607.00, which is what `region_revenue` and `customers.customer_lifetime_value` show. Those models are only hit by B.
- The credit card column is 1706 = 2 × 853, and 871 − 853 = 18.00 of card payments with NULL amounts.
- `orders` is hit by both A and B, so it reads 2460.

**Why every test passed:**

- The four tests on the seeds (`raw_payment_methods` and `raw_customer_regions`) are not run by this `dbt build`. The log has 28 tests against the 32 found, because the raw tables are loaded outside dbt.
- No test checks uniqueness of `raw_payment_methods.payment_method`, NULL amounts in `stg_payments`, or reconciliation to the processor.

**A third problem: `daily_revenue` is stale.**

- It only rebuilds the last three days. January shows 496 vs 668 in `orders`, February 415 vs 679, and March 622 vs 950. Only April matches.
- It still holds values from earlier runs, so even after the fix it needs a `--full-refresh`.

## 2. Fix

**Data:**
1. Remove the duplicate `credit_card` row from `raw_payment_methods`. Both rows have the same `fee_rate`, so only the processor name differs. Keep whichever processor is current; I can't tell which that is from the data.
2. Have ingestion reload the NULL-amount rows in `raw_payments` from the processor. Don't `coalesce` them to 0, because that hides the gap.

**Code:** make `orders.sql` safe even if the lookup is dirty. Dedupe the lookup before the join:

```sql
payment_methods as (

    select
        payment_method,
        max(fee_rate) as fee_rate
    from {{ ref('raw_payment_methods') }}
    group by payment_method

),
```

Then rebuild everything, including the incremental table:

```
dbt build --full-refresh
```

Reconcile afterwards: `orders.amount` should equal the 1672.00 settlement once the NULLs are reloaded.

## 3. Tests that would have caught it

Add these to `models/schema.yml`:

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - not_null
          - unique

models:
  - name: stg_payments
    columns:
      - name: amount
        tests:
          - not_null
```

Add a singular reconciliation test, `tests/assert_orders_amount_matches_payments.sql`. It fails if `orders` doesn't equal staged payments in total or per payment method:

```sql
with o as (
    select sum(amount) as amt,
           sum(credit_card_amount) as cc,
           sum(coupon_amount) as coupon,
           sum(bank_transfer_amount) as bank,
           sum(gift_card_amount) as gift
    from {{ ref('orders') }}
),
p as (
    select sum(amount) as amt,
           sum(case when payment_method = 'credit_card' then amount end) as cc,
           sum(case when payment_method = 'coupon' then amount end) as coupon,
           sum(case when payment_method = 'bank_transfer' then amount end) as bank,
           sum(case when payment_method = 'gift_card' then amount end) as gift
    from {{ ref('stg_payments') }}
)
select *
from o cross join p
where o.amt is distinct from p.amt
   or o.cc is distinct from p.cc
   or o.coupon is distinct from p.coupon
   or o.bank is distinct from p.bank
   or o.gift is distinct from p.gift
```

- The duplicate row fails the `unique` test. The NULLs fail `not_null` on `stg_payments.amount`.
- The reconciliation test also catches any future fan-out.
- Since the raw tables are loaded outside dbt, make sure the nightly job runs these tests, for example `dbt test --select raw_payment_methods stg_payments orders`. The seed tests are currently skipped.

I haven't touched any files. The seed CSVs and ingestion job weren't in what you pasted, so the data fixes are for you to apply.
