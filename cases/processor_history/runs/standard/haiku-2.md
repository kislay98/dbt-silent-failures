claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 53 s | USD 0.009 | 2026-10-15 | project ad0b16b | prompt b0b450385f10

## 1. Root cause

**The `raw_payment_methods` seed has two `credit_card` rows, and `orders.sql` joins on `payment_method` alone.**

- `jaffle_shop/seeds/raw_payment_methods.csv` (inferred path, from `seed-paths`) contains:
  - `credit_card | stripe | 0.029 | 2018-01-01`
  - `credit_card | adyen | 0.029 | 2018-03-01`
- `models/orders.sql` joins `payments` to `payment_methods` with `on payments.payment_method = payment_methods.payment_method`. Each credit-card payment matches both rows, so its amount is counted twice in `order_payments`. `bank_transfer`, `coupon`, and `gift_card` each have one row, so they are unaffected.
- This explains every symptom:
  - Credit card is doubled in every month (1742 vs 871).
  - `orders.amount` is `sum(payments.amount)` over the fanned-out rows, so it is 2543 = 1672 + 871.
  - Order 1 shows 20.00 for a 10.00 payment.
  - `fees` is also doubled for credit card.
  - `customers.customer_lifetime_value` and `region_revenue` do not join the fee seed, which is why they still total 1672.
- The tests passed because the fan-out happens inside the `order_payments` CTE and is collapsed by `group by order_id`. `orders` still has one row per order, so `unique`/`not_null` pass. No test reconciles amounts back to `stg_payments`.
- The seed contract is also violated: its description says "One row per payment method", but the table has two. The `effective_from` column suggests the processor changed on 2018-03-01 and the rate should be chosen by date.

**Related finding in `daily_revenue`:** the incremental model only rebuilds the last three days. Its Jan–Mar rows are correct (e.g. Jan 496), but April is doubled (163; correct is 139). Its total is 1696, not 1672. The stale rows need a full refresh after the fix.

## 2. Fix

Pick the fee rate in effect on each order date, with one row per payment. Replace the `payment_methods` join in `jaffle_shop/models/orders.sql` with this:

```sql
{% set payment_methods = ['credit_card', 'coupon', 'bank_transfer', 'gift_card'] %}

with orders as (

    select * from {{ ref('stg_orders') }}

),

payments as (

    select * from {{ ref('stg_payments') }}

),

payment_methods as (

    {#-
    Processor and fee rate per payment method, loaded as a seed like the other raw tables
    #}
    select * from {{ ref('raw_payment_methods') }}

),

-- one row per payment, using the fee rate in effect on the order date
-- (a payment method can have several rows over time, e.g. a processor change)
payment_rates as (

    select
        payments.payment_id,
        payments.order_id,
        payments.payment_method,
        payments.amount,
        payment_methods.fee_rate

    from payments

    left join orders
        on payments.order_id = orders.order_id

    left join payment_methods
        on payments.payment_method = payment_methods.payment_method
        and orders.order_date >= payment_methods.effective_from

    qualify row_number() over (
        partition by payments.payment_id
        order by payment_methods.effective_from desc nulls last
    ) = 1

),

order_payments as (

    select
        order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payment_method = '{{ payment_method }}' then amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(amount) as total_amount,
        round(sum(amount * coalesce(fee_rate, 0)), 2) as fees

    from payment_rates

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

Also update the seed's description in `models/schema.yml` to say "one row per payment method per `effective_from`", so the contract matches the data.

**Rebuild** (the full refresh is needed for `daily_revenue`, whose history is stale):

```
dbt build --select orders+ --full-refresh
```

**Expected results after the fix:** `sum(orders.amount)` = 1672.00, `sum(credit_card_amount)` = 871.00 (Jan–Apr), order 1 credit card = 10.00, `daily_revenue` total = 1672.00.

**Data-only alternative:** if the stripe row is retired and the feed is meant to hold one current row per method, deleting the stale `credit_card | stripe` row would fix the numbers without a model change. The seed has no `effective_to` column, so I can't tell from the files which reading is correct. The point-in-time join works either way.

## 3. Test that would have caught it

Add a singular test that reconciles each payment method's total in `orders` against the payments source:

```sql
-- tests/orders_reconcile_to_payments.sql
with expected as (

    select payment_method, sum(amount) as expected_amount
    from {{ ref('stg_payments') }}
    group by payment_method

),

actual as (

    select 'credit_card' as payment_method, sum(credit_card_amount) as actual_amount from {{ ref('orders') }}
    union all
    select 'coupon', sum(coupon_amount) from {{ ref('orders') }}
    union all
    select 'bank_transfer', sum(bank_transfer_amount) from {{ ref('orders') }}
    union all
    select 'gift_card', sum(gift_card_amount) from {{ ref('orders') }}

)

select expected.payment_method, expected.expected_amount, actual.actual_amount
from expected
join actual using (payment_method)
where abs(expected.expected_amount - actual.actual_amount) > 0.005
```

On the current code this returns a `credit_card` row (1742 vs 871) and fails. After the fix it passes.

Optionally, add a seed-level check that `(payment_method, effective_from)` is unique. This needs the `dbt_utils` package, which isn't visible in the project, so I have not added it.
