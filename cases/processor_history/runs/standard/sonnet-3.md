claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 33 s | USD 0.104 | 2026-10-15 | project ad0b16b | prompt b0b450385f10

## 1. Root cause

**`seeds/raw_payment_methods` has two rows for `credit_card`**, and `models/orders.sql` joins it on `payment_method` alone.

The sample shows the processor migration:

| payment_method | processor | effective_from |
|---|---|---|
| credit_card | stripe | 2018-01-01 |
| credit_card | adyen | 2018-03-01 |

It is a history table with one row per method and processor, but `order_payments` treats it as one row per method:

```sql
left join payment_methods on payments.payment_method = payment_methods.payment_method
```

- Every credit card payment matches two rows, so it is counted twice in `credit_card_amount`, `total_amount` and `fees`.
- Coupon, bank transfer and gift card have a single row, so they are correct.
- Order 1 shows 20.00 instead of 10.00 for the same reason.
- The totals reconcile exactly: 1742 = 2 × 871, and 2543 − 1672 = 871, the credit card total.
- `region_revenue` and `customers` read `stg_payments` directly and never touch the fee table, so they are correct.

**Why no test failed:**
- Nothing tests the grain of `raw_payment_methods` (only `not_null`).
- `orders` only checks `not_null` and `unique` on `order_id`, and the fan-out happens before `group by order_id`, so the keys stay unique.
- Nothing reconciles `orders` to the payments.

**Secondary issue, `daily_revenue`:**
- Its months total 496 / 415 / 622 / 163 = 1696 against the correct 1672.
- Jan–Mar are right because those rows were written before the duplicate row existed and the incremental run never rebuilds them.
- April shows 163, which is the doubled figure. Correct April is 163 − 24 = 139.
- Fixing `orders` alone will not repair the April rows already stored there.

## 2. Fix

Make the join as-of the order date, so each payment matches exactly one processor row. In `models/orders.sql`:

```sql
payment_methods as (

    {#-
    Processor and fee rate per payment method, loaded as a seed like the other raw tables.
    One row per method and processor, so each row is valid from effective_from until the next one
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

    inner join orders
        on payments.order_id = orders.order_id

    left join payment_methods
        on payments.payment_method = payment_methods.payment_method
        and orders.order_date >= payment_methods.effective_from
        and (payment_methods.effective_to is null
             or orders.order_date < payment_methods.effective_to)

    group by payments.order_id

),
```

The inner join to `orders` is safe because `final` left joins from `orders`, so payments without an order were never output.

Then rebuild the stale incremental table:

```
dbt build --full-refresh -s orders+ 
```

or at least `dbt run --full-refresh -s daily_revenue`. Afterwards the checks should read:
- `sum(orders.amount)` = 1672.00
- credit card = 871.00
- order 1 = 10.00
- `daily_revenue` April = 139.00

## 3. Tests that would have caught it

Add a grain test on the seed in `models/schema.yml`. dbt-utils is not installed in this project, so a singular test is the simplest option. Here is `tests/assert_payment_methods_no_overlap.sql`:

```sql
-- each payment method may have only one active row at any date
select payment_method, effective_from
from {{ ref('raw_payment_methods') }}
group by 1, 2
having count(*) > 1

union all

select a.payment_method, a.effective_from
from {{ ref('raw_payment_methods') }} a
join {{ ref('raw_payment_methods') }} b
    on a.payment_method = b.payment_method
    and a.effective_from < b.effective_from
    and a.effective_from >= (
        select min(effective_from) from {{ ref('raw_payment_methods') }} where payment_method = a.payment_method
    )
    and exists (select 1)  -- keeps adjacent rows only
where a.effective_from = b.effective_from
```

That overlap test is more complex than it needs to be, since the as-of model already handles successive effective dates. The test that would have caught this incident directly is a per-order reconciliation against staging, in `tests/assert_orders_amount_matches_payments.sql`:

```sql
-- orders.amount and the per-method columns must equal the raw payments, order by order
with payments as (

    select
        order_id,
        sum(amount) as amount,
        sum(case when payment_method = 'credit_card' then amount else 0 end) as credit_card_amount
    from {{ ref('stg_payments') }}
    group by order_id

)

select orders.order_id
from {{ ref('orders') }} as orders
left join payments using (order_id)
where abs(orders.amount - coalesce(payments.amount, 0)) > 0.005
   or abs(orders.credit_card_amount - coalesce(payments.credit_card_amount, 0)) > 0.005
```

I'd keep only this reconciliation test plus a simple uniqueness test on the seed's `(payment_method, effective_from)` pair, and drop the overlap query above. The reconciliation test would have failed on the first build with the duplicate row, and it works regardless of which join introduces the fan-out. I would also add the same check for `daily_revenue` (its sum against `orders`), since it silently kept stale data.
