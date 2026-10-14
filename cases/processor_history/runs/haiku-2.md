claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 36 s | USD 0.006 | 2026-10-14 | project f769fdf+wk6ext | prompt 44b209d47f7f

## 1. Root cause

`jaffle_shop/models/orders.sql` joins `payments` to the `raw_payment_methods` seed on `payment_method` alone, and does so before aggregating:

```sql
from payments
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
```

The seed has an `effective_from` column, which means it keeps rate history. If `credit_card` has more than one row, each credit-card payment is multiplied by the number of matching rows before `sum()` runs. The `group by payments.order_id` then collapses the rows back to one per order, so `unique` and `not_null` still pass, but the amounts are inflated.

The numbers fit this pattern:

- **Credit card is exactly doubled**, and only credit card is affected. Order 1 has a 10.00 payment and shows 20.00. Order 13 has a 5.00 card payment and shows 10.00, while its 14.00 bank transfer is correct.
- **Totals:** 871.00 of credit-card payments × 2 = 1742.00. `sum(orders.amount)` = 1672.00 + 871.00 = 2543.00.
- **`customers.customer_lifetime_value` and `region_revenue` are correct** because they sum `stg_payments` directly and never touch the seed join.

I haven't seen the seed rows, so the two-rows-for-credit-card explanation is inferred from the doubling and the `effective_from` column. Confirm it with:

```sql
select payment_method, count(*) from raw_payment_methods group by 1;
```

**Why the tests pass:** none of them compare totals. `unique(order_id)` and `not_null` are satisfied because the duplication happens inside the aggregate. The `fees` column is inflated by the same fan-out.

## 2. Fix

Separate the amounts from the fees, so the amounts never touch the seed and the fee lookup returns one row per method.

Replace `jaffle_shop/models/orders.sql` with:

```sql
{% set payment_methods = ['credit_card', 'coupon', 'bank_transfer', 'gift_card'] %}

with orders as (

    select * from {{ ref('stg_orders') }}

),

payments as (

    select * from {{ ref('stg_payments') }}

),

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

-- the seed holds rate history, so take the latest rate per method
-- (joining it directly duplicated payments before aggregation)
payment_rates as (

    select payment_method, fee_rate
    from {{ ref('raw_payment_methods') }}
    qualify row_number() over (partition by payment_method order by effective_from desc) = 1

),

order_fees as (

    select
        payments.order_id,
        round(sum(payments.amount * coalesce(payment_rates.fee_rate, 0)), 2) as fees

    from payments

    left join payment_rates
        on payments.payment_method = payment_rates.payment_method

    group by payments.order_id

),

final as (

    select
        orders.order_id,
        orders.customer_id,
        orders.order_date,
        orders.status,

        {% for payment_method in payment_methods -%}

        order_payments.{{ payment_method }}_amount,

        {% endfor -%}

        order_payments.total_amount as amount,
        order_fees.fees

    from orders

    left join order_payments
        on orders.order_id = order_payments.order_id

    left join order_fees
        on orders.order_id = order_fees.order_id

)

select * from final
```

**Decision for finance:** this applies the latest fee rate to all historical orders. If the rate changed during Jan to Apr and past fees must reflect the rate at the time, join the rates on `effective_from <= orders.order_date` and pick the latest matching row per payment. That is a business call, so I haven't built it in.

**Also rebuild `daily_revenue`.** It sums `orders.amount`, so it is inflated too. It is incremental and only rebuilds the last three days, so the old days stay wrong until you run:

```
dbt run -s daily_revenue --full-refresh
```

Then run `dbt build` to refresh `orders` and everything downstream.

## 3. Test that would have caught this

Add `jaffle_shop/tests/orders_reconcile_to_payments.sql`. It fails if the orders' per-method totals don't match the payments:

```sql
-- every payment must land in exactly one orders row, so per-method totals must match stg_payments
with expected as (

    select payment_method, sum(amount) as total
    from {{ ref('stg_payments') }}
    group by payment_method

),

actual as (

    select 'credit_card' as payment_method, sum(credit_card_amount) as total from {{ ref('orders') }}
    union all
    select 'coupon', sum(coupon_amount) from {{ ref('orders') }}
    union all
    select 'bank_transfer', sum(bank_transfer_amount) from {{ ref('orders') }}
    union all
    select 'gift_card', sum(gift_card_amount) from {{ ref('orders') }}

)

select
    expected.payment_method,
    expected.total as payments_total,
    actual.total as orders_total
from expected
join actual using (payment_method)
where abs(expected.total - actual.total) > 0.005
```

This test is the one that matters. It checks the actual invariant, that every payment appears exactly once, so it catches duplication from any source. With the current data it would have failed on `credit_card` (871.00 vs 1742.00).

Adding a `unique` test on `raw_customer_regions` and `raw_payment_methods` for their join keys would also flag the fan-out at the source. The `raw_payment_methods` one would fail today if it has rate history, so use it alongside the reconciliation test, not instead of it.
