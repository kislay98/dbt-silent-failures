claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 28 s | USD 0.180 | 2026-10-15 | project ad0b16b | prompt 037c69d41959

# Root cause: duplicate `bank_transfer` row in `raw_payment_methods` fans out the join in `orders.sql`

## 1. Root cause

**The data:** `raw_payment_methods` should have one row per payment method, as its description says. It has two rows for `bank_transfer`, one per processor:

```
bank_transfer │ anz     │ 0.005
bank_transfer │ westpac │ 0.005
```

That's why it has 5 rows for 4 methods.

**The file:** `jaffle_shop/models/orders.sql`, CTE `order_payments`:

```sql
from payments
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
group by payments.order_id
```

The join only exists to look up `fee_rate`. Because of the duplicate row, every `bank_transfer` payment matches twice and gets summed twice. That doubles `bank_transfer_amount`, `total_amount` (exposed as `amount`) and the bank-transfer part of `fees`. The numbers confirm it exactly:

- 2083 − 1672 = **411**, which is the true bank-transfer total. Each month's `bank_transfer` (272/148/324/78 = 822) is exactly 2× the statement.
- Order 5: a 17.00 bank transfer shows as 34.00.
- Credit card, coupon and gift card have one row each, so they reconcile.
- `customers` and `region_revenue` read `stg_payments` directly and never join `raw_payment_methods`, so they still total 1672.

**Why the tests pass:**
- `orders.order_id` is still unique because the fan-out is collapsed by `group by order_id`.
- The seed only has `not_null` tests on `payment_method`, with no `unique` test.
- Nothing reconciles `orders.amount` against `stg_payments`.

**Side effect:** `daily_revenue` is incremental and reads `orders`. It totals 1696, a mix of correct older days and inflated recent days (April: 163 vs 178 in `orders`). It needs a full refresh after the fix.

## 2. Fix

**Data fix (the real fix):** reduce `raw_payment_methods` to one row per method, so `bank_transfer` has a single processor. Ask finance or the ingestion owner which processor is current (anz or westpac) and remove the other row at the source feed or ingestion job.

**Code hardening:** stop the fee lookup from being able to change amounts, whatever the grain of the lookup table. In `orders.sql`:

```sql
payment_methods as (

    {#-
    Processor and fee rate per payment method, loaded as a seed like the other raw tables.
    Collapsed to one row per method so the fee lookup can never fan out payments.
    #}
    select
        payment_method,
        max(fee_rate) as fee_rate
    from {{ ref('raw_payment_methods') }}
    group by payment_method

),
```

`max()` is harmless here because both rows are 0.005. Don't rely on it silently, though; keep the uniqueness test below so a genuine conflict fails the build.

Then rebuild, including a full refresh of the incremental model:

```bash
dbt build --select orders+ --full-refresh
```

## 3. Tests that would have caught this

**Grain test on the lookup table** (`models/schema.yml`, under `raw_payment_methods`):

```yaml
      - name: payment_method
        tests:
          - not_null
          - unique
```

**Reconciliation test**, which catches any fan-out or drop regardless of cause. Add `tests/assert_orders_amount_matches_payments.sql`:

```sql
-- orders.amount must equal the sum of that order's payments
with payments as (

    select order_id, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by order_id

)

select
    orders.order_id,
    orders.amount,
    payments.amount as payments_amount

from {{ ref('orders') }} as orders

left join payments
    on orders.order_id = payments.order_id

where abs(coalesce(orders.amount, 0) - coalesce(payments.amount, 0)) > 0.001
```

On the current data this fails on every order with a bank-transfer payment, order 5 included.

## Unrelated issue

`schema.yml` documents a `customers.total_order_amount` column, but the model outputs `customer_lifetime_value`. Worth renaming one of them so the docs match.
