claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 59 s | USD 0.010 | 2026-10-15 | project ad0b16b | prompt b0b450385f10

## 1. Root cause

**The bug is in `jaffle_shop/models/orders.sql`. The seed data is valid history, but the model doesn't handle it.**

`raw_payment_methods` has two `credit_card` rows, one for `stripe` (effective 2018-01-01) and one for `adyen` (effective 2018-03-01). Both have fee_rate 0.029, which looks like a processor switch. The `order_payments` CTE joins payments to this table on `payment_method` alone:

```sql
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
```

Every credit card payment therefore matches two seed rows and is emitted twice. The `sum(case when ... credit_card ...)`, `sum(payments.amount)`, and fee sums all run over the duplicated rows. The effect:

- `credit_card_amount` is doubled (1742 vs 871).
- `amount` is doubled for any order with a credit card payment. Order 1 is 20.00 instead of 10.00.
- `fees` are doubled for credit card payments as well.
- `sum(orders.amount)` = 1672 + 871 = 2543, which is exactly the observed value.
- Coupon, bank transfer and gift card are unaffected because each has one seed row.

`customers.customer_lifetime_value` and `region_revenue` read from `stg_payments` without this join, so they stay at 1672, as observed.

**Why the tests passed:** the join fans out inside the aggregation, so `orders` still has one row per order. `unique(order_id)` and `not_null` pass. No test reconciles amounts. In addition:

- The seed's documented contract ("one row per payment method") is violated by the data. Nothing checks it.
- `raw_payment_methods` has no uniqueness test, and the seed tests didn't run at all. The build log shows 28 data tests executed out of the 32 declared, and 7 models + 28 tests = 35. The 4 seed tests were never run.

**Related, `models/daily_revenue.sql`:** it's downstream of the bad `orders.amount`. Its incremental window only rebuilds from `max(order_date) - 3 days`, so older rows keep values from earlier runs. Its revenue total is 1696, which matches neither 1672 nor 2543. April (163) matches the doubled `orders` value, and the older months are stale.

## 2. Fix

Two changes to `orders.sql`. First, amounts and fees are computed separately, so a fee-rate join can never inflate amounts again. Second, fees use the rate in effect on the order date.

```sql
{% set payment_methods = ['credit_card', 'coupon', 'bank_transfer', 'gift_card'] %}

with orders as (

    select * from {{ ref('stg_orders') }}

),

payments as (

    select * from {{ ref('stg_payments') }}

),

-- amounts come straight from payments, so nothing here can fan out
order_amounts as (

    select
        payments.order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payments.payment_method = '{{ payment_method }}' then payments.amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(payments.amount) as total_amount

    from payments
    group by payments.order_id

),

-- one fee rate per payment: the rate in effect on the order date
order_fees as (

    select
        payments.order_id,
        round(sum(payments.amount * coalesce(payment_methods.fee_rate, 0)), 2) as fees

    from payments
    join orders
        on payments.order_id = orders.order_id
    asof left join {{ ref('raw_payment_methods') }} as payment_methods
        on payments.payment_method = payment_methods.payment_method
        and orders.order_date >= payment_methods.effective_from

    group by payments.order_id

),

final as (

    select
        orders.order_id,
        orders.customer_id,
        orders.order_date,
        orders.status,

        {% for payment_method in payment_methods -%}
        order_amounts.{{ payment_method }}_amount,
        {% endfor -%}

        order_amounts.total_amount as amount,
        order_fees.fees

    from orders
    left join order_amounts
        on orders.order_id = order_amounts.order_id
    left join order_fees
        on orders.order_id = order_fees.order_id

)

select * from final
```

Notes:
- `ASOF LEFT JOIN` is DuckDB syntax. If the project moves warehouses, replace it with a subquery that picks the latest `effective_from <= order_date` per method.
- Both credit card rates are 0.029, so the fees for this data come out the same whichever row matches. The point-in-time join matters if the rates diverge later.
- The seed itself doesn't need changing. The `schema.yml` description ("one row per payment method") is wrong for this data and should say "one row per payment method per effective period".

**Rebuild steps:**
1. `dbt run --select orders daily_revenue --full-refresh` (the full refresh is needed because the incremental model won't correct old dates).
2. Check that `sum(orders.amount)` is 1672.00 and the monthly credit card totals match the processor statements (Jan 436/2, etc.; the table above halves to the statement values).
3. Confirm order 1 is 10.00.
4. Make sure the nightly job runs the seed tests (see below). The fix is verified by reading the SQL and the log, not by running it against your warehouse.

## 3. A test that would have caught this

**a) Reconciliation of orders to payments (catches this bug on any data, including order 1):**

```sql
-- tests/assert_order_amounts_match_payments.sql
select o.order_id
from {{ ref('orders') }} as o
join (
    select order_id, sum(amount) as paid
    from {{ ref('stg_payments') }}
    group by order_id
) as p
    on o.order_id = p.order_id
where abs(o.amount - p.paid) > 0.005
   or abs(o.credit_card_amount + o.coupon_amount + o.bank_transfer_amount + o.gift_card_amount - o.amount) > 0.005
```

A singular test returns failing rows, so this fails the build on any mismatch.

**b) Uniqueness of the seed's effective periods (catches the duplicate rows at the source):**

```sql
-- tests/assert_payment_method_rates_unique_per_period.sql
select payment_method, effective_from, count(*) as n
from {{ ref('raw_payment_methods') }}
group by payment_method, effective_from
having count(*) > 1
```

Also make sure the nightly command includes the seed tests. Check the selector or any `--exclude resource_type:seed` in the job. With them included, the build would have run 32 data tests, not 28.
