claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 55 s | USD 0.009 | 2026-10-15 | project ad0b16b | prompt b0b450385f10

## 1. Root cause

**`jaffle_shop/models/orders.sql`**, in the `order_payments` CTE, joins payments to `raw_payment_methods` on `payment_method` alone:

```sql
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
```

`raw_payment_methods` is a rate history, not a lookup table. It has two `credit_card` rows, one for stripe (`effective_from` 2018-01-01) and one for adyen (`effective_from` 2018-03-01), both at fee rate 0.029. Each credit card payment therefore matches two rows and is duplicated before the `group by order_id`. The `sum(...)` calls in `order_payments` then double-count card payments, and so does the `fees` sum.

The grouping hides the fan-out. `orders` still has one row per order, so `unique_orders_order_id` passes. Nothing compares the totals back to `stg_payments`, so no test fails.

The output confirms this:

- Credit card: 2 × 871 = 1742.
- `sum(amount)`: 1672 (true) + 871 (the extra card money) = 2543.
- Order 1: a 10.00 charge shows as 20.00.
- Order 25 fees: 2 × (22 × 0.029) + 20 × 0.005 = 1.376, which rounds to the 1.38 shown.

Downstream:

- `customers.customer_lifetime_value` and `region_revenue` read `stg_payments` directly, so they correctly total 1672.
- `daily_revenue` is incremental and only rebuilds the last three days. Its total of 1696 is 24 above the true 1672, which is consistent with the doubled card money in the rebuilt window. Older rows were built before the bug and are still correct, so the table is in a mixed state.

The raw data is not wrong. The bug is that the model ignores `effective_from`.

**Test gap:** the four seed tests in `schema.yml` never ran. The build found 32 data tests and ran 28, and the log has no seed nodes. The 4 missing tests are the seed tests. Seeds are not part of this build, so their tests do not gate the nightly job.

## 2. Fix

Price each payment at the rate in effect on its order date, one row per payment, before aggregating. Replace the `payment_methods` and `order_payments` CTEs in `models/orders.sql` with:

```sql
{% set payment_methods = ['credit_card', 'coupon', 'bank_transfer', 'gift_card'] %}

with orders as (

    select * from {{ ref('stg_orders') }}

),

payments as (

    select * from {{ ref('stg_payments') }}

),

payment_rates as (

    {#-
    Rate history per payment method, keyed by effective_from. Several rows per method are expected
    #}
    select * from {{ ref('raw_payment_methods') }}

),

-- one row per payment, priced at the rate in force on the order date
priced_payments as (

    select
        payments.order_id,
        payments.payment_method,
        payments.amount,
        coalesce(payment_rates.fee_rate, 0) as fee_rate

    from payments

    join orders
        on payments.order_id = orders.order_id

    asof left join payment_rates
        on payments.payment_method = payment_rates.payment_method
        and orders.order_date >= payment_rates.effective_from

),

order_payments as (

    select
        order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payment_method = '{{ payment_method }}' then amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(amount) as total_amount,
        round(sum(amount * fee_rate), 2) as fees

    from priced_payments

    group by order_id

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
        order_payments.fees

    from orders

    left join order_payments
        on orders.order_id = order_payments.order_id

)

select * from final
```

Notes on the fix:

- `ASOF LEFT JOIN` is DuckDB syntax, which fits this project. It picks the latest rate row whose `effective_from` is on or before the order date. If you need a portable version, use a `qualify row_number() over (partition by payment_id order by effective_from desc) = 1` instead.
- `stg_payments` has no payment timestamp, so the order date stands in for the payment date. Confirm that is acceptable.
- Both credit card rates are 0.029, so fees change only because the duplication is removed. Card fees will roughly halve.
- Do not delete the adyen row from the seed. It is legitimate rate history.
- Update the `fees` description in `schema.yml` to say "fee rate in effect on the order date".

Rebuild, with a full refresh because `daily_revenue` only rebuilds its last three days:

```
dbt build --select orders+ --full-refresh
```

Expected results after the rebuild: credit card 871.00, `sum(amount)` 1672.00, order 1 at 10.00. `daily_revenue` should total 1672.00. `customers` and `region_revenue` should stay at 1672.00.

I have not run this. The expected figures are derived from the logged outputs.

## 3. Test that would have caught it

Add a singular test that reconciles each order to its payments in the source. It fails on the current data (order 1: 20.00 vs 10.00) and passes after the fix.

**`jaffle_shop/tests/assert_order_amount_matches_payments.sql`**

```sql
-- each order's amount must equal the sum of its payments in stg_payments
with payment_totals as (

    select
        order_id,
        sum(amount) as payments_total

    from {{ ref('stg_payments') }}

    group by order_id

)

select
    orders.order_id,
    orders.amount,
    payment_totals.payments_total

from {{ ref('orders') }} as orders

left join payment_totals
    on orders.order_id = payment_totals.order_id

where abs(coalesce(orders.amount, 0) - coalesce(payment_totals.payments_total, 0)) > 0.005
```

Because it runs as a test node, it executes even though the seeds are not built. It would also have flagged `credit_card_amount` if you add a per-method version.

A `unique` test on `raw_payment_methods.payment_method` would not have caught this. The duplicate `credit_card` rows are valid history with different `effective_from` dates. To make sure the four seed tests run, add the seeds to the nightly `dbt build` selection, or confirm why they are excluded.
