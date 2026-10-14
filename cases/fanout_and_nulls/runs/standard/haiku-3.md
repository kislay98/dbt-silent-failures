claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 71 s | USD 0.011 | 2026-10-14 | project ad0b16b | prompt 1db8a43ee8e9

## Summary

There are two separate faults, and together they explain every number in the report.

| Figure | Value | Explanation |
|---|---|---|
| Raw payments, non-null, all methods | 1607.00 | Equals `customers.customer_lifetime_value` total, which doesn't go through the fan-out |
| Processor settlement | 1672.00 | 1607 + 65 missing |
| `orders.amount` | 2460.00 | 1607 + 853 duplicated credit-card amount |
| `credit_card_amount` | 1706.00 | 2 × 853 |

## 1. Root cause

**A. Fan-out from a duplicated `credit_card` row (the overstatement).**
`raw_payment_methods` has two `credit_card` rows, one for `stripe` and one for `adyen`, both at fee_rate 0.029. In `jaffle_shop/models/orders.sql`, `order_payments` does this:

```sql
from payments
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
```

Every credit-card payment joins to two rows, so it is counted twice in every `sum(...)`. This doubles `credit_card_amount` (1706 = 2 × 853), inflates `amount` (`sum(payments.amount)`) by the 853 of credit-card payments, and doubles the fee for credit-card orders. Order 2 going from 20.00 to 40.00 fits this exactly.

The seed is `raw_payment_methods`. Whether the duplicate lives in `seeds/raw_payment_methods.csv` or in the warehouse table depends on what the ingestion job loads, so check both.

**B. Missing payment amounts in the raw data (the shortfall).**
Relative to the processor, the raw payments are short by 65.00: credit card 18.00, bank transfer 25.00, coupon 16.00, gift card 6.00. The model sums what it receives, so bank, coupon and gift-card output matches raw exactly (no fan-out there).

The sample shows the likely cause. `raw_payments` has NULL amounts (id 30, coupon, order 25; id 55, credit card, order 49). `stg_payments.sql` passes `amount / 100` through as NULL, and `sum()` silently drops it.

Order 13 confirms the pattern. Its only visible payment is id 14, 500 cents (5.00). The output shows 10.00, which is 2 × 5.00 from the fan-out. The 14.00 that makes up the 19.00 settlement is absent from `raw_payments`. This fix belongs in the ingestion job, not in dbt.

**C. Why every test passed.**
- `raw_payment_methods` has only `not_null` tests. Nothing checks that `payment_method` is unique, so the duplicate passed.
- `stg_payments.amount` has no `not_null` test, so NULL amounts pass.
- `orders.amount` and the per-method columns only have `not_null`. No test ties `amount` to the payments.
- The log shows 32 data tests found and 28 run. The four missing are the seed tests (`raw_customer_regions`, `raw_payment_methods`), and no seed nodes appear in the run. The nightly build isn't exercising the seed tests at all.
- `daily_revenue` is also stale. It is incremental and only rebuilds the last three days. Its January–March revenue (496 / 415 / 622) doesn't match `orders` (668 / 679 / 950), even though the order counts match. Only April (163) agrees.

I inferred B from the arithmetic and the sample. I haven't run queries against the warehouse, so confirm with:

```sql
select payment_method, count(*) from raw_payment_methods group by 1 having count(*) > 1;
select payment_method, count(*), sum(amount) from raw_payments where amount is null group by 1;
select payment_method, sum(amount) / 100.0 as dollars from raw_payments group by 1;
```

## 2. Fix

**Data (the actual source of the error)**
1. Decide which credit-card processor is active. Remove the other row from `raw_payment_methods` in whichever place the ingestion job reads it. The fee rates match, so fees don't depend on the choice.
2. Fix the ingestion of `raw_payments` so NULL amounts and missing payments are resolved. After that, the non-null total should reach 1672.00 by method: credit card 871.00, bank transfer +25.00, coupon +16.00, gift card +6.00.

**Code (so a duplicate can't fan out again)**
In `jaffle_shop/models/orders.sql`, collapse the seed to one row per method before the join:

```sql
payment_methods as (

    select payment_method, fee_rate
    from {{ ref('raw_payment_methods') }}
    -- placeholder: keep one row per method until the active processor is confirmed
    qualify row_number() over (partition by payment_method order by processor) = 1

),
```

**Rebuild**
Run `dbt build --select daily_revenue --full-refresh`. A normal incremental run won't correct the historical rows.

## 3. A test that would have caught it

Add a singular test at `jaffle_shop/tests/assert_order_amount_matches_payments.sql`. It fails on the duplicated credit-card row:

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

Also add these to `schema.yml`:
- `raw_payment_methods.payment_method`: `unique`. This is the key the join assumes.
- `stg_payments.amount`: `not_null`. This catches the NULL amounts.

Last, make sure the nightly `dbt build` includes seeds, so the seed tests actually run.
