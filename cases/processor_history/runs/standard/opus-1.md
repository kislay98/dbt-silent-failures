claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 38 s | USD 0.203 | 2026-10-15 | project ad0b16b | prompt b0b450385f10

## 1. Root cause

**`models/orders.sql` joins payments to `raw_payment_methods` on `payment_method` alone, but that table now has two `credit_card` rows.** Each credit-card payment matches both rows, so it is counted twice.

```
credit_card │ stripe │ 0.029 │ 2018-01-01
credit_card │ adyen  │ 0.029 │ 2018-03-01   ← new row: processor switch effective March
```

`raw_payment_methods` is effective-dated, with one row per method per `effective_from` and 5 rows for 4 methods. The join in `order_payments` ignores `effective_from`:

```sql
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
```

The duplication explains every symptom:

- **Credit card is doubled in every month.** Both rows match regardless of date, so January and February are doubled too. 1742 = 2 × 871.
- **Order total is overstated by exactly the credit-card amount.** 2543 − 1672 = 871. `total_amount` is summed over the duplicated rows.
- **Order 1 shows 20.00 instead of 10.00.** That order was a credit-card payment.
- **Bank transfer, coupon and gift card are correct.** Those methods have only one row each.
- **`fees` is also doubled for credit card.** For example, January's 13.35 is wrong. Finance hasn't noticed this yet.
- **`customers` and `region_revenue` are correct (1672).** They read `stg_payments` directly and never touch `raw_payment_methods`.
- **`daily_revenue` is partly wrong.** Its months are 496 / 415 / 622 / 163. January to March are correct because those days were built before the Adyen row arrived and are outside the 3-day rebuild window. April's 163 already contains the doubled credit card; it should be 163 − 24 = 139. It will keep absorbing bad data on each run.

No test caught this for three reasons:
- Every test on `orders` is `not_null` or `unique` on `order_id`, and the fan-out happens before the `group by`, so the grain is still one row per order.
- The seed tests on `raw_payment_methods` are `not_null` only.
- Those seed tests don't run in the nightly build anyway. The log shows "32 data tests" found, but only 28 run, because seeds are excluded.

## 2. Fix

In `models/orders.sql`, make the lookup effective-dated so each payment matches exactly one fee row. Payments have no date, so use the order date:

```sql
payments as (

    select
        stg_payments.*,
        stg_orders.order_date

    from {{ ref('stg_payments') }} as stg_payments

    left join {{ ref('stg_orders') }} as stg_orders
        on stg_payments.order_id = stg_orders.order_id

),

payment_methods as (

    {#-
    Processor and fee rate per payment method, effective-dated: one row per method per effective_from
    #}
    select
        payment_method,
        processor,
        fee_rate,
        effective_from,
        lead(effective_from) over (partition by payment_method order by effective_from) as effective_to

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

    left join payment_methods
        on payments.payment_method = payment_methods.payment_method
        and payments.order_date >= payment_methods.effective_from
        and (payments.order_date < payment_methods.effective_to or payment_methods.effective_to is null)

    group by payments.order_id

),
```

The `final` CTE stays unchanged.

After the change, rebuild with a full refresh so the polluted April days in `daily_revenue` are replaced:

```
dbt build --select orders+ --full-refresh
```

Expected results:
- `sum(orders.amount)` = 1672.00
- `sum(credit_card_amount)` = 871.00
- April in `daily_revenue` = 139.00

## 3. Tests that would have caught it

**a) Reconciliation test on `orders`** (`tests/assert_orders_amount_matches_payments.sql`). This catches any fan-out regardless of its cause:

```sql
-- orders.amount must equal the sum of that order's payments; any join fan-out breaks this
with payments as (

    select order_id, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by order_id

)

select
    orders.order_id,
    orders.amount,
    payments.amount as payments_amount

from {{ ref('orders') }} as orders

full outer join payments
    on orders.order_id = payments.order_id

where abs(coalesce(orders.amount, 0) - coalesce(payments.amount, 0)) > 0.005
```

On the current data, this test fails for every order that has a credit-card payment.

**b) Grain test on the lookup** (`tests/assert_payment_methods_one_row_per_effective_date.sql`). It is written as a singular test so it runs in the nightly build even though seeds are excluded:

```sql
select payment_method, effective_from, count(*) as rows
from {{ ref('raw_payment_methods') }}
group by 1, 2
having count(*) > 1
```

A plain `unique` test on `payment_method` would also have caught the new row. However, it would now be wrong, because multiple rows per method are legitimate in this table. The correct grain is `(payment_method, effective_from)`. Test (a) is the one that protects the finance numbers.

One unrelated issue: in `models/schema.yml`, the `customers` model documents a column `total_order_amount`, but the model outputs `customer_lifetime_value`. That should be renamed in the YAML.
