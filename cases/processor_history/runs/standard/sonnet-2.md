claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 24 s | USD 0.095 | 2026-10-15 | project ad0b16b | prompt b0b450385f10

## 1. Root cause

**`seeds/raw_payment_methods.csv`, joined in `models/orders.sql`, fans out every credit card payment.**

The seed is effective-dated and holds two `credit_card` rows: `stripe` from 2018-01-01 and `adyen` from 2018-03-01, both at 0.029. The seed has 5 rows for 4 payment methods. `orders.sql` joins `payments` to it on `payment_method` alone:

```sql
left join payment_methods on payments.payment_method = payment_methods.payment_method
```

Each credit card payment therefore matches two rows and is counted twice. Coupon, bank transfer and gift card have one row each, so they stay correct.

- **`credit_card_amount`, `amount`:** these are `sum(...)` over the fanned-out rows, so credit card is doubled (1742 vs 871) and `amount` is overstated by exactly that (2543 vs 1672). Order 1 shows 20.00 instead of 10.00.
- **`fees`:** also doubled on credit card, because it uses the same fanned-out rows.
- **`region_revenue` and `customers`:** they read `stg_payments` directly and never touch the seed, so they stay at 1672.
- **Tests:** none of them check the grain of the join or reconcile `orders` to payments. `unique(order_id)` still passes because the `group by` collapses the fan-out back to one row per order.

**`daily_revenue`:** it is also wrong. It is incremental and only rebuilds the last 3 days, so earlier days keep whatever values were computed under earlier states of the seed. Its monthly sum is 1696, which matches neither 1672 nor 2543. April is 163, which includes the doubled credit card amounts.

## 2. Fix

Pick the one fee-rate row that was in effect on the order date. In `orders.sql`:

```sql
payment_methods as (

    {#-
    Processor and fee rate per payment method, effective-dated. A method can have
    several rows (e.g. a processor change), so bound each row by the next one.
    #}
    select
        payment_method,
        fee_rate,
        effective_from,
        lead(effective_from) over (
            partition by payment_method order by effective_from
        ) as effective_to
    from {{ ref('raw_payment_methods') }}

),

order_payments as (

    select
        payments.order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payments.payment_method = '{{ payment_method }}' then payments.amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(payments.amount) as total_amount,
        round(sum(payments.amount * coalesce(payment_methods.fee_rate, 0)), 2) as fees

    from payments

    join orders
        on payments.order_id = orders.order_id

    left join payment_methods
        on payments.payment_method = payment_methods.payment_method
        and orders.order_date >= payment_methods.effective_from
        and (payment_methods.effective_to is null
             or orders.order_date < payment_methods.effective_to)

    group by payments.order_id

),
```

Then rebuild `daily_revenue` from scratch so the stale history is replaced:

```
dbt build --full-refresh -s orders daily_revenue+
```

After this, credit card should be 871.00 and `sum(orders.amount)` 1672.00. Fees should drop to about half of what they are now. This works because the rates are identical before and after the switch. If they differed, the old rate would apply to January and February and the new one from March.

## 3. Tests that would have caught it

Add a singular test, `tests/assert_orders_reconcile_to_payments.sql`:

```sql
-- orders must carry exactly the payments loaded, in total and per method
with o as (
    select
        round(sum(amount), 2)             as amount,
        round(sum(credit_card_amount), 2) as credit_card,
        round(sum(coupon_amount), 2)      as coupon,
        round(sum(bank_transfer_amount), 2) as bank_transfer,
        round(sum(gift_card_amount), 2)   as gift_card
    from {{ ref('orders') }}
),
p as (
    select
        round(sum(amount), 2) as amount,
        round(sum(case when payment_method = 'credit_card' then amount end), 2)   as credit_card,
        round(sum(case when payment_method = 'coupon' then amount end), 2)        as coupon,
        round(sum(case when payment_method = 'bank_transfer' then amount end), 2) as bank_transfer,
        round(sum(case when payment_method = 'gift_card' then amount end), 2)     as gift_card
    from {{ ref('stg_payments') }}
)
select * from o cross join p
where o.amount        != p.amount
   or o.credit_card   != p.credit_card
   or o.coupon        != p.coupon
   or o.bank_transfer != p.bank_transfer
   or o.gift_card     != p.gift_card
```

It returns rows, and so fails, on the current data. Two cheaper guards help as well:

- **Seed grain:** a `dbt_utils.unique_combination_of_columns` test on `raw_payment_methods` over `(payment_method, effective_from)`, so a duplicated effective date is rejected.
- **Revenue agreement:** a test that `daily_revenue` total equals `orders` total, which would have exposed the stale incremental history.
