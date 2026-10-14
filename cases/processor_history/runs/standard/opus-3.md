claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 47 s | USD 0.220 | 2026-10-15 | project ad0b16b | prompt b0b450385f10

## 1. Root cause

**`raw_payment_methods` now has two `credit_card` rows, and `models/orders.sql` joins to it on `payment_method` alone.**

```
credit_card │ stripe │ 0.029 │ 2018-01-01
credit_card │ adyen  │ 0.029 │ 2018-03-01   ← new row: processor switch from March
```

The table is no longer "one row per payment method". It is effective-dated, keyed by `(payment_method, effective_from)`. The `order_payments` CTE in `orders.sql` does this:

```sql
from payments
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
```

That join ignores `effective_from`. Every credit card payment matches both rows before the `group by payments.order_id`, so it is summed twice. The numbers line up exactly:

- **Credit card is doubled in every month, including Jan and Feb.** That happens before adyen's effective date, so the join is ignoring dates rather than picking the wrong processor. The true total is 1742 / 2 = 871.
- **`amount` is overstated by exactly the credit card total:** 2543 − 1672 = 871.
- **Order 1:** a 10.00 credit card payment becomes 20.00.
- **Fees** are also doubled on the credit card portion.
- **Unaffected outputs:** bank transfer, coupon and gift card have a single row each. `customers` and `region_revenue` read `stg_payments` directly and never join `raw_payment_methods`, so they still total 1672.

**Why every test passed:** fan-out happens *before* the group by, so `orders` still has one row per `order_id`. `unique`, `not_null` and `relationships` all hold. Nothing compares amounts to their source. The seed only has `not_null` tests, so the duplicate key went unnoticed. There is no `unique` test on `payment_method`, and it would now be wrong anyway.

**Knock-on effect in `daily_revenue`:** it is incremental and only rebuilds the last 3 days. Jan–Mar (496 / 415 / 622) are still correct because they were built before the adyen row arrived. April shows 163, but the correct value is 139 (163 − 24 of doubled credit card). Fixing `orders` will **not** repair rows outside the lookback window.

## 2. Fix

Don't delete the adyen row. The history is legitimate and the processor really changed. Instead, make the join effective-dated, using the order date, because payments carry no date of their own.

`models/orders.sql`, replacing the `payment_methods` and `order_payments` CTEs:

```sql
payment_methods as (

    {#-
    Processor and fee rate per payment method, loaded as a seed like the other raw tables.
    Effective-dated: one row per payment method per effective_from
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

    join orders
        on payments.order_id = orders.order_id

    left join payment_methods
        on payments.payment_method = payment_methods.payment_method
        and orders.order_date >= payment_methods.effective_from
        and (payment_methods.effective_to is null or orders.order_date < payment_methods.effective_to)

    group by payments.order_id

),
```

The inner join to `orders` loses nothing, because `final` already left-joins `orders` to `order_payments`. Update the seed description in `schema.yml` to "One row per payment method per `effective_from`".

Then rebuild, including a full refresh of the incremental model so April is corrected:

```sh
dbt build -s orders+ --full-refresh
```

Expected after the fix: credit card 871.00, `sum(orders.amount)` 1672.00, order 1 = 10.00, `daily_revenue` April = 139.00.

## 3. Tests that would have caught it

The key test is a reconciliation against the source. A uniqueness test on `(payment_method, effective_from)` would **not** have caught this, because the two rows have different dates. Any fan-out in a join, from this table or a future one, breaks the amounts, so test the amounts.

`tests/assert_orders_amount_matches_payments.sql`:

```sql
-- orders.amount must equal the sum of that order's payments; a fan-out in any join breaks this
with payments as (

    select order_id, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by order_id

)

select
    coalesce(orders.order_id, payments.order_id) as order_id,
    orders.amount as order_amount,
    payments.amount as payment_amount

from {{ ref('orders') }} as orders

full outer join payments
    on orders.order_id = payments.order_id

where abs(coalesce(orders.amount, 0) - coalesce(payments.amount, 0)) > 0.005
```

This would have failed on every order with a credit card payment, starting with order 1 (20 vs 10).

Two cheap additions are worth having too:

- **`daily_revenue` reconciliation:** `sum(daily_revenue.revenue)` = `sum(orders.amount)`. This catches stale incremental rows like April's.
- **Seed guard on `raw_payment_methods`:** a `unique_combination_of_columns` test on `[payment_method, effective_from]` (dbt_utils, or an equivalent `group by … having count(*) > 1` singular test). This documents the real grain of the table.

Unrelated, but noticed while reading: `schema.yml` documents a `customers.total_order_amount` column. The model actually outputs `customer_lifetime_value`.
