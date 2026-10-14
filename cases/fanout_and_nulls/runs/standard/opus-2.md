claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 53 s | USD 0.232 | 2026-10-14 | project ad0b16b | prompt 1db8a43ee8e9

# Two independent faults stack up here

Your numbers reconcile exactly once you split the problem into two causes.

| | Processor | Warehouse rows | `orders` |
|---|---|---|---|
| credit_card | 871 | 853 (−18 NULL) | **1706** (853 × 2) |
| bank_transfer | — | −25 NULL | −25 |
| coupon | — | −16 NULL | −16 |
| gift_card | — | −6 NULL | −6 |
| **Total** | **1672** | **1607** (−65) | **2460** (1607 + 853) |

`region_revenue` and `customers` don't join to `raw_payment_methods`. They only show the NULL loss: 1672 − 65 = 1607.

## 1. Root cause

### A. `raw_payment_methods` has two `credit_card` rows, so `models/orders.sql` fans out

The seed now has `credit_card / stripe / 0.029` and `credit_card / adyen / 0.029`. In `order_payments`, `payments left join payment_methods on payment_method` matches every credit-card payment twice. The `group by payments.order_id` hides the extra rows: `orders` still has 99 unique rows, so `unique_orders_order_id` passes. But `credit_card_amount`, `amount` and the credit-card part of `fees` are all doubled.

- **Order 2:** a single 20.00 card payment shows as 40.00.

The seed's `schema.yml` says "one row per payment method", but nothing enforces it.

### B. `raw_payments.amount` is NULL for payments that were actually settled

The ingestion job loaded payments with `amount = NULL`, for example id 30 (order 25, coupon) and id 55 (order 49, credit_card). Together they are worth 65.00: 18 credit card, 25 bank transfer, 16 coupon, 6 gift card. `sum()` skips NULLs silently. `stg_payments.amount` has no `not_null` test, and the `not_null` tests on `orders.*_amount` pass because `sum(case … else 0 end)` turns a NULL into a value.

- **Order 13:** it was settled for 19.00. The warehouse has a 5.00 card payment, doubled to 10.00 by fault A, and the other 14.00 payment is NULL.

### `daily_revenue` confirms the timeline

Its Jan–Mar rows are 496 + 415 + 622 = 1533. They were built before either fault existed and match the settlement. The April rows were rebuilt by the incremental window and hold 163 instead of the correct 139, which is +24 of doubled credit card. 1533 + 139 = 1672 ✓.

Also note: the nightly run executed 28 of the 32 data tests. The 4 seed tests (on `raw_customer_regions` and `raw_payment_methods`) never ran, because seeds are excluded from the selection and their tests are excluded with them.

## 2. Fix

**Data:**
1. Re-run or backfill `raw_payments` from the processor so the NULL amounts are restored. Find them with `select * from raw_payments where amount is null`; their sum should come to 6500 cents. Don't `coalesce(amount, 0)` in dbt. That would make the gap permanent instead of fixing it.
2. Make `raw_payment_methods` one row per `payment_method`. If processor matters, it has to live on the payment row, not on the method lookup.

**Code:** make `orders.sql` immune to the fan-out:

```sql
payment_methods as (

    {#-
    Processor and fee rate per payment method, loaded as a seed like the other raw tables.
    Collapsed to one row per method so the join below can't fan out payments
    #}
    select
        payment_method,
        max(fee_rate) as fee_rate
    from {{ ref('raw_payment_methods') }}
    group by payment_method

),
```

Both credit-card rows have rate 0.029, so this gives the correct fees today. The uniqueness test below flags the seed if the rates ever diverge.

**Rebuild:**
```
dbt build --full-refresh --select daily_revenue+ orders+ customers region_revenue
```
`--full-refresh` is required for `daily_revenue`. The incremental window only rebuilds dates from `max(order_date) − 3 days` onward, so the inflated rows for April 1–5 would otherwise stay wrong.

## 3. Tests that would have caught it

```yaml
# models/schema.yml, under seeds: raw_payment_methods
      - name: payment_method
        tests:
          - not_null
          - unique

# models/staging/schema.yml, under stg_payments
      - name: amount
        tests:
          - not_null
```

A reconciliation test catches any fan-out or row loss between staging and marts, whatever the cause:

```sql
-- tests/assert_order_amounts_reconcile_to_payments.sql
with payments as (
    select sum(amount) as total from {{ ref('stg_payments') }}
),

orders as (
    select sum(amount) as total from {{ ref('orders') }}
)

select payments.total as payments_total, orders.total as orders_total
from payments, orders
where abs(coalesce(payments.total, 0) - coalesce(orders.total, 0)) > 0.005
```

This one would have failed tonight: 1607 vs 2460. Also make sure the nightly selector includes the seed tests, for example `dbt test --select resource_type:seed` or dropping the seed exclusion. Otherwise the `unique` test on `raw_payment_methods` never runs.
