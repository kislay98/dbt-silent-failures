claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 40 s | USD 0.006 | 2026-10-14 | project f769fdf+wk6ext | prompt b430ab6cd05c

## 1. Root cause

**`jaffle_shop/models/orders.sql`** fans out payment rows when it joins to the payment-method seed. The join is:

```sql
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
```

`raw_payment_methods` (`seeds/raw_payment_methods.csv`, read through `models/orders.sql`) appears to have **two rows for `bank_transfer`**, one per processor. Every bank-transfer payment then joins to both rows, so each one is counted twice in the `sum(...)` calls. The seed data wasn't included, so this is inferred from the numbers, but the pattern matches exactly:

- **Only bank transfer is doubled.** Credit card, coupon and gift card each have one row, so they don't fan out. Their totals match the statements.
- **Order 5:** one bank-transfer payment of 17.00 is counted twice, giving 34.00.
- **Total:** 2083.00 − 1672.00 = 411.00, which is exactly the bank-transfer total in `stg_payments`.
- **Monthly:** bank transfer is 822.00 against 411.00 in every month, consistent with a fan-out that is not time-dependent.

The fan-out also inflates `fees`, because the fee is computed inside the same join. The fee error is larger or smaller than the amount error depending on the fee rates in the duplicate rows.

Downstream effects:
- `orders.amount` and `orders.bank_transfer_amount` are wrong, which is what finance sees.
- `daily_revenue` sums `orders.amount`, so it is also inflated. It is incremental with a 3-day rebuild, so **older days stay wrong until a full refresh**.
- `region_revenue` and `customers.customer_lifetime_value` are correct (1672.00) because they sum `stg_payments.amount` directly and never go through `orders.amount` or the payment-method join.

**Why no test caught it:**
- `orders.order_id` is still unique. The fan-out multiplies sums, not rows.
- The not-null and accepted-values tests still pass.
- `raw_payment_methods` has no uniqueness test, and its two seed tests never ran. The log shows 32 data tests found but only 28 run, and it has no seed nodes. The seeds are loaded by the ingestion job and aren't part of this `dbt build`, so their tests are skipped.

To confirm the duplicate before changing anything:

```sql
select payment_method, processor, fee_rate
from raw_payment_methods
order by payment_method;
```

I expect two `bank_transfer` rows.

## 2. Fix

**a) Fix the data.** Find out which `bank_transfer` row is current, probably the processor still in use, and confirm its fee rate with the processor or finance. Remove the stale row in the ingestion job's source. dbt can't tell which one is right, so this step needs a person.

**b) Stop the model from fanning out.** Add a staging model that is part of the build, so its test runs:

`models/staging/stg_payment_methods.sql`
```sql
select
    payment_method,
    processor,
    fee_rate

from {{ ref('raw_payment_methods') }}
```

`models/staging/schema.yml`, add:
```yaml
  - name: stg_payment_methods
    columns:
      - name: payment_method
        tests:
          - unique
          - not_null
```

`models/orders.sql`, change the seed reference to the staged model:
```sql
payment_methods as (

    select * from {{ ref('stg_payment_methods') }}

),
```

If the duplicate comes back, the `unique` test fails and `dbt build` skips `orders` and its dependents instead of publishing wrong totals.

**c) Rebuild history.** `daily_revenue` won't correct older days on its normal run:

```bash
dbt build --select orders+ --full-refresh
```

## 3. Tests that would have caught it

**Primary: a reconciliation test against the source payments.** This catches the problem whatever its cause.

`tests/assert_orders_match_payments.sql`
```sql
with paid as (

    select
        order_id,
        payment_method,
        sum(amount) as amount

    from {{ ref('stg_payments') }}
    group by 1, 2

),

expected as (

    select
        o.order_id,
        'bank_transfer' as payment_method,
        o.bank_transfer_amount as actual

    from {{ ref('orders') }} o

    union all

    select order_id, 'credit_card', credit_card_amount from {{ ref('orders') }}
    union all
    select order_id, 'coupon', coupon_amount from {{ ref('orders') }}
    union all
    select order_id, 'gift_card', gift_card_amount from {{ ref('orders') }}

)

select
    e.order_id,
    e.payment_method,
    e.actual,
    coalesce(p.amount, 0) as expected

from expected e
left join paid p
    on e.order_id = p.order_id
    and e.payment_method = p.payment_method

where abs(e.actual - coalesce(p.amount, 0)) > 0.005
```

Order 5 would fail this with actual 34.00 against expected 17.00. A simpler version, `sum(orders.amount)` equal to `sum(stg_payments.amount)`, would also have flagged the 2083 against 1672 mismatch.

**Regression: a dbt unit test for the join.** This pins the fan-out bug directly:

`models/unit_tests.yml`
```yaml
unit_tests:
  - name: orders_do_not_fan_out_on_duplicate_payment_methods
    model: orders
    given:
      - input: ref('stg_orders')
        rows:
          - {order_id: 5, customer_id: 64, order_date: 2018-01-05, status: completed}
      - input: ref('stg_payments')
        rows:
          - {payment_id: 5, order_id: 5, payment_method: bank_transfer, amount: 17}
      - input: ref('stg_payment_methods')
        rows:
          - {payment_method: bank_transfer, processor: a, fee_rate: 0.01}
          - {payment_method: bank_transfer, processor: b, fee_rate: 0.01}
    expect:
      rows:
        - {order_id: 5, bank_transfer_amount: 17, amount: 17}
```

This test fails on the current code and passes after the fix, and it runs without touching the warehouse data.

**Other things I noticed:**
- `schema.yml` documents `customers.total_order_amount`, which the model doesn't produce.
- The `raw_customer_regions` seed has an `updated_at` column, so it may later get multiple rows per customer. That would fan out `region_revenue` in the same way. Add the same `unique` test on `customer_id` in a staging model.
