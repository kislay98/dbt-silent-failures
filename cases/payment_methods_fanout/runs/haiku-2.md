claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 37 s | USD 0.006 | 2026-10-14 | project f769fdf+wk6ext | prompt b430ab6cd05c

## 1. Root cause

**`jaffle_shop/models/orders.sql`, in the `order_payments` CTE, joins `payments` to `raw_payment_methods` on `payment_method`. That seed has more than one row for `bank_transfer`, so every bank-transfer payment is duplicated before the aggregation runs.** The duplicate rows are in the seed data, which isn't in the files you sent, so confirm with:

```sql
select payment_method, count(*) from raw_payment_methods group by 1 having count(*) > 1;
```

The output matches a fan-out on exactly one payment method:

- `sum(orders.amount)` 2083 − 1672 = **411**, which is exactly the bank-transfer total in `stg_payments`.
- Bank transfer is 822 = 2 × 411, in every month. Other methods are correct.
- Order 5: payment 17 → `bank_transfer_amount` 34.
- Order 25: payments are bank 20, card 22, coupon 16 (total 58), but `amount` is 78 = 58 + 20.
- `fees` on bank-transfer payments are also computed over both joined rows.

The `group by payments.order_id` collapses the duplicated rows back to one row per order, so the `unique` and `not_null` tests on `orders` still pass. The aggregation doubles the amounts instead of creating visible duplicate rows.

Why the rest of the project is fine:

- `region_revenue` and `customers.customer_lifetime_value` sum `stg_payments.amount` directly and never join through `raw_payment_methods`, so they still total 1672.
- `daily_revenue` sums `orders.amount`, so it is inflated too.

The seed's own description in `schema.yml` says "One row per payment method", but nothing enforces it: the seed has `not_null` on `payment_method` and no `unique` test.

## 2. Fix

**Data:** make `raw_payment_methods` one row per `payment_method`. If bank transfer really has two processors with different rates, the fee rate can't be chosen from `payment_method` alone, and `stg_payments` would need the processor to pick the right rate. Decide that with finance and the processor contract; don't guess from the sample.

**Model:** keep amounts independent of the fee join, so a bad fee seed can never change revenue. Replace the `order_payments` CTE and the `final` select in `orders.sql` with:

```sql
payment_amounts as (

    select
        order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payment_method = '{{ payment_method }}' then amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(amount) as total_amount

    from payments

    group by order_id

),

payment_fees as (

    select
        payments.order_id,
        round(sum(payments.amount * coalesce(payment_methods.fee_rate, 0)), 2) as fees

    from payments

    left join payment_methods
        on payments.payment_method = payment_methods.payment_method

    group by payments.order_id

),

final as (

    select
        orders.order_id,
        orders.customer_id,
        orders.order_date,
        orders.status,

        {% for payment_method in payment_methods -%}
        payment_amounts.{{ payment_method }}_amount,
        {% endfor -%}

        payment_amounts.total_amount as amount,
        payment_fees.fees

    from orders

    left join payment_amounts
        on orders.order_id = payment_amounts.order_id

    left join payment_fees
        on orders.order_id = payment_fees.order_id

)
```

Then fix the data and rebuild. `daily_revenue` is incremental and only rebuilds the last three days, so older rows stay wrong until you full-refresh it:

```
dbt build --select orders+ --full-refresh
```

Check the bank-transfer fee rate on the 4 bank-transfer orders in the sample (fees 0.17 on 17, 0.26 on 26) after the seed fix. Right now the two rows sum to 0.01 per dollar, so one row is probably a duplicate or stale rate.

## 3. Test that would have caught it

The test that catches the symptom regardless of cause is a reconciliation singular test. Create `jaffle_shop/tests/assert_order_amount_matches_payments.sql`:

```sql
select
    orders.order_id,
    orders.amount,
    payments.total
from {{ ref('orders') }} as orders
join (
    select order_id, sum(amount) as total
    from {{ ref('stg_payments') }}
    group by order_id
) as payments
    on orders.order_id = payments.order_id
where abs(orders.amount - payments.total) > 0.005
```

For the cause itself, add a `unique` test on the seed in `seeds/schema.yml`, or in the `seeds:` section of `jaffle_shop/models/schema.yml`:

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - unique
          - not_null
```

This second test fails at the source, before any model runs. The reconciliation test is the one that would have failed on the 2083 vs 1672 mismatch even if someone changed the seed later.
