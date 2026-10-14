claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 28 s | USD 0.099 | 2026-10-15 | project ad0b16b | prompt b0b450385f10

## 1. Root cause

**`seeds/raw_payment_methods.csv` has two rows for `credit_card`**: `stripe` (effective 2018-01-01) and `adyen` (effective 2018-03-01), both at 0.029. The seed is a processor history with one row per method and processor, but its schema.yml description says "one row per payment method".

**`models/orders.sql`** (CTE `order_payments`) joins on `payments.payment_method = payment_methods.payment_method` alone. It ignores `effective_from`, so every credit card payment matches both rows and is counted twice. That produces the symptoms:

- `credit_card_amount` and `amount` double-count credit card payments. Order 1 shows 20.00 instead of 10.00.
- The excess is exactly the card volume: 2543 − 1672 = 871, the processor statement figure.
- `fees` on card payments are doubled too, and it affects every month, including January and February, which predate the Adyen row.
- Coupon, bank transfer and gift card each have one row in the seed, so they are correct.
- `region_revenue` and `customers` read `stg_payments` directly and never touch the seed, so they still total 1672.

No test fails because the checks are only `not_null`, `unique` and `accepted_values`, and none compare totals. `unique(order_id)` still passes because the `group by` re-collapses the fanned-out rows.

**Secondary symptom: `daily_revenue`.** It totals 1696, not 1672. It is incremental and only rebuilds the last 3 days. Older days kept their earlier correct values, while the rebuilt window picked up the doubled card amounts (24 extra). It needs a full refresh after the fix.

## 2. Fix

In `orders.sql`, resolve the rate that was effective on the order date, so each payment matches exactly one seed row:

```sql
payment_methods as (

    {#-
    Processor and fee rate per payment method, loaded as a seed like the other raw tables.
    One row per method and effective period, so we turn it into validity ranges.
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
        and (orders.order_date < payment_methods.effective_to
             or payment_methods.effective_to is null)

    group by payments.order_id

),
```

Also fix the seed description in `models/schema.yml` to say it has one row per method and effective period. Then rebuild, since the incremental table keeps its stale rows:

```
dbt build --full-refresh
```

Expected result: `sum(orders.amount)` = 1672.00, credit card = 871.00 and order 1 = 10.00. Card fees will also fall to about half of their current values.

## 3. Tests that would have caught it

**Reconciliation test** (`tests/assert_orders_reconcile_to_payments.sql`), which fails if any order differs from its payments:

```sql
select
    o.order_id,
    o.amount as orders_amount,
    p.amount as payments_amount
from {{ ref('orders') }} o
join (
    select order_id, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by order_id
) p using (order_id)
where abs(o.amount - p.amount) > 0.005
   or abs(o.credit_card_amount + o.coupon_amount + o.bank_transfer_amount + o.gift_card_amount - o.amount) > 0.005
```

**Overall total** (`tests/assert_orders_total_equals_payments_total.sql`):

```sql
select o.total, p.total
from (select sum(amount) as total from {{ ref('orders') }}) o,
     (select sum(amount) as total from {{ ref('stg_payments') }}) p
where abs(o.total - p.total) > 0.005
```

**Seed test:** the effective periods must not overlap. The `lead()` logic assumes that, and a `unique` test on `payment_method` alone would have flagged the duplicate. Use `dbt_utils.unique_combination_of_columns` on `(payment_method, effective_from)` as a minimum.

**`daily_revenue`:** add a similar check that `sum(revenue)` equals `sum(orders.amount)`. That would have flagged the stale incremental history.
