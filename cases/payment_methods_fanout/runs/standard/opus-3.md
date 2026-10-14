claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 38 s | USD 0.200 | 2026-10-15 | project ad0b16b | prompt 037c69d41959

# Root cause: duplicate `bank_transfer` rows in `raw_payment_methods` fan out the join in `orders.sql`

## 1. Root cause

**The data:** `raw_payment_methods` has 5 rows for 4 payment methods. `bank_transfer` appears twice, once per processor:

```
bank_transfer │ anz     │ 0.005
bank_transfer │ westpac │ 0.005
```

The seed's description says "One row per payment method", but the feed is actually one row per (payment_method, processor).

**The code:** `models/orders.sql`, CTE `order_payments`, does `payments left join payment_methods on payment_method` before `group by order_id`. Every bank-transfer payment matches two rows, so it is summed twice into `bank_transfer_amount`, `total_amount` (exposed as `orders.amount`) and `fees`.

The numbers support this exactly:
- `bank_transfer` is 822 = 2 × 411.
- `amount` is 2083 = 1672 + 411. Only the bank-transfer amount is added twice.
- Credit card, coupon and gift card are unaffected because they have one matching row each.
- Order 5 shows 34 = 2 × 17. Order 90 shows 4.0 with fee 0.02, which is 2 × (2.00 × 0.005).
- `customers` and `region_revenue` read `stg_payments` directly and never join `raw_payment_methods`, so they still total 1672.

**Knock-on effect, `daily_revenue`:** it totals 1696, not 2083 or 1672. Jan to Mar match the true figures (496, 415 and 622). April is 163 against a true 139. This is because the incremental model only rebuilds the last three days, so only those days picked up the doubled `orders.amount`. Older rows were built before the duplicate seed row arrived.

**Why no test failed:**
- The only test on `raw_payment_methods.payment_method` is `not_null`, with no `unique`.
- The build log shows those seed tests never ran. The run found 32 data tests but executed 28, and no seeds were selected.
- Nothing reconciles `orders.amount` against `stg_payments`.

## 2. Fix

In `models/orders.sql`, collapse the lookup to one row per payment method before joining. That way a multi-processor method can't multiply payments:

```sql
payment_methods as (

    {#-
    The processor feed has one row per payment method and processor (bank_transfer goes through
    anz and westpac), so collapse it to one fee rate per payment method before joining to payments
    #}
    select
        payment_method,
        max(fee_rate) as fee_rate

    from {{ ref('raw_payment_methods') }}

    group by payment_method

),
```

`max` is safe only while all processors for a method charge the same rate (they do today, 0.005). The test below guards that assumption. If the rates diverge, you would need a processor on `raw_payments` to pick the right one.

Fix the seed's documented grain in `models/schema.yml`. The description should say "One row per payment method and processor".

Then rebuild, including a full refresh of the incremental model so the polluted April rows are replaced:

```
dbt build --select orders+ --full-refresh
```

## 3. Tests that would have caught it

**Reconciliation test.** This is the one that directly catches the symptom finance saw. Add `tests/assert_order_amounts_match_payments.sql`:

```sql
-- orders.amount must equal the sum of the order's payments; any join fan-out or loss shows up here
with payments as (

    select order_id, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by order_id

)

select
    coalesce(orders.order_id, payments.order_id) as order_id,
    orders.amount as order_amount,
    payments.amount as payment_amount

from {{ ref('orders') }} as orders

full outer join payments
    on orders.order_id = payments.order_id

where coalesce(orders.amount, 0) <> coalesce(payments.amount, 0)
```

With the current data this fails on every order that has a bank transfer, for example order 5 with 34 against 17.

**Grain tests on the seed.** Add these to `models/schema.yml`:

```yaml
  - name: raw_payment_methods
    description: One row per payment method and processor from the processor feed, with its fee rate
    tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['payment_method', 'processor']
```

Add a singular test, `tests/assert_one_fee_rate_per_payment_method.sql`, to protect the `max(fee_rate)` assumption:

```sql
select payment_method
from {{ ref('raw_payment_methods') }}
group by payment_method
having count(distinct fee_rate) > 1
```

**Make sure seed tests actually run.** The nightly build currently skips them; only 28 of 32 tests ran. Either include the seeds in the build selection or add a step for them, such as `dbt test --select resource_type:seed+1` or `dbt test --select raw_payment_methods raw_customer_regions`.

`raw_customer_regions` also deserves a `unique` test on `customer_id`. It has an `updated_at` column, so a second row for a customer would fan out `region_revenue` in exactly the same way.
