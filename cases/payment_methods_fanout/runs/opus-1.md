claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 51 s | USD 0.179 | 2026-10-14 | project f769fdf+wk6ext | prompt 335d8f2d0911

## 1. Root cause

**`raw_payment_methods` contains two rows for `bank_transfer`** (most likely two processors), and **`models/orders.sql`** joins every payment to that table before aggregating:

```sql
from payments
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
group by payments.order_id
```

Every bank-transfer payment matches two rows, so it is counted twice in `bank_transfer_amount`, `total_amount` and `fees`. The output confirms this:

- **Order 5:** one 17.00 bank transfer becomes 34.00.
- **Order 25:** the real payments are 20 + 22 + 16 = 58, but `amount` is 78 because the bank transfer is counted as 40.
- **Totals:** 2083 − 1672 = 411, which is exactly the bank-transfer total in `stg_payments`. Bank transfer shows 822 = 2 × 411. Credit card, coupon and gift card each have one row and are correct.
- **Unaffected models:** `region_revenue` and `customers` read `stg_payments` without joining `raw_payment_methods`, so they still total 1672.
- **Fees are also wrong.** Bank transfers get charged the sum of both rows' rates, about 1% in total (17 → 0.17, 26 → 0.26), instead of the single correct rate.
- **`daily_revenue` is inflated too,** because it is built from `orders`.

**Why no test caught it:**
- Nothing checks that `payment_method` is unique in `raw_payment_methods`.
- The tests that do exist on the seed (`not_null` on `payment_method` and `fee_rate`) never run nightly. The log reports "32 data tests" found but only 28 run. Seeds are excluded from the build because ingestion loads them, and excluding the seeds also drops their tests.
- The `not_null` and `unique(order_id)` tests on `orders` cannot detect a fan-out that happens before the `group by`.

To confirm the duplicate:

```sql
select payment_method, count(*) as n, list(processor) as processors, list(fee_rate) as fee_rates
from raw_payment_methods group by 1 having count(*) > 1;
```

## 2. Fix

**Data:** fix the ingestion job that loads `raw_payment_methods` so it produces exactly one row per `payment_method`. Delete the duplicate `bank_transfer` row, checking with finance which processor and fee rate is current. If bank transfers really do use two processors, the fee can't be computed per method anyway: `raw_payments` would need a `processor` column.

**Code:** change `orders.sql` so that payment amounts can never fan out. Compute the amounts from `payments` alone and the fees separately:

```sql
order_payments as (

    select
        order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payment_method = '{{ payment_method }}' then amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(amount) as total_amount

    from payments

    group by order_id

),

order_fees as (

    select
        payments.order_id,
        round(sum(payments.amount * coalesce(payment_methods.fee_rate, 0)), 2) as fees

    from payments

    left join payment_methods
        on payments.payment_method = payment_methods.payment_method

    group by payments.order_id

),
```

In `final`, take `fees` from `order_fees`, joined on `order_id`. Fees still depend on `raw_payment_methods` being unique, which the test below enforces.

**After fixing:** run `dbt build --full-refresh -s orders+`. `daily_revenue` is incremental and only rebuilds the last 3 days, so without a full refresh January to April would stay inflated.

## 3. Tests that would have caught this

**a) Uniqueness of the payment-method lookup.** Declare the ingested tables as sources rather than seeds so their tests run nightly, or include the seed tests in the build selection:

```yaml
# models/sources.yml
sources:
  - name: raw
    schema: main
    tables:
      - name: raw_payment_methods
        columns:
          - name: payment_method
            tests: [unique, not_null]
          - name: fee_rate
            tests: [not_null]
```

**b) Reconciliation of orders against payments.** This catches any fan-out, whatever causes it:

```sql
-- tests/assert_orders_amount_matches_payments.sql
with payments as (
    select order_id, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by order_id
),

orders as (
    select order_id, amount
    from {{ ref('orders') }}
    where amount is not null
)

select
    coalesce(orders.order_id, payments.order_id) as order_id,
    orders.amount as orders_amount,
    payments.amount as payments_amount
from orders
full outer join payments on orders.order_id = payments.order_id
where abs(coalesce(orders.amount, 0) - coalesce(payments.amount, 0)) > 0.005
```

Test (b) would have failed on 33 bank-transfer orders. Test (a) would have flagged the duplicate row directly.

Unrelated, but worth fixing: `schema.yml` documents a `customers.total_order_amount` column that doesn't exist. The model outputs `customer_lifetime_value`.
