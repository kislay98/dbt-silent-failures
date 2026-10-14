claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 25 s | USD 0.073 | 2026-10-14 | project f769fdf+wk6ext | prompt 44b209d47f7f

## 1. Root cause

**`models/orders.sql`, join to `raw_payment_methods` (the `order_payments` CTE).**

The seed `raw_payment_methods` is not one row per payment method. Its loaded columns include `effective_from`, so it is a fee-rate history with one row per method per rate period. The model joins on `payment_method` alone:

```sql
left join payment_methods on payments.payment_method = payment_methods.payment_method
```

The data shows `credit_card` has two rows in that seed (a rate change). Each credit card payment matches both rows and is counted twice. The other three methods have a single row, so they are unaffected.

- Order 1: the 10.00 payment appears as 20.00, and order 2 shows 40.00 for a 20.00 payment.
- Order 13: credit card 5.00 becomes 10.00, while bank transfer 14.00 is untouched.
- Totals: credit card is 1742 = 2 × 871, and `sum(amount)` is 2543 = 1672 + 871.
- **`fees` is also wrong.** For order 1, 0.58 is 10.00 × (rate₁ + rate₂), so both historical rates are applied to every credit card payment.
- `region_revenue` and `customers` read `stg_payments` directly and never touch the seed, which is why they still total 1672.00.
- Nothing failed because the tests only check `not_null`, `unique` and `accepted_values`. There is no uniqueness test on the seed's grain and no reconciliation of `orders` to `stg_payments`. `orders.order_id` stays unique because the fan-out happens before the `group by`.

`daily_revenue` is built from `orders`, so it is inflated too.

## 2. Fix

Pick the single fee rate in effect on the order date. In `orders.sql`, replace the `payment_methods` and `order_payments` CTEs:

```sql
payment_methods as (

    {#-
    Fee rate history per payment method. Turn effective_from into a [valid_from, valid_to) range
    so each payment matches exactly one row. The first rate is treated as open-ended backwards.
    #}
    select
        payment_method,
        fee_rate,
        case
            when row_number() over (partition by payment_method order by effective_from) = 1
                then cast('1900-01-01' as date)
            else effective_from
        end as valid_from,
        coalesce(
            lead(effective_from) over (partition by payment_method order by effective_from),
            cast('9999-12-31' as date)
        ) as valid_to
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
        and orders.order_date >= payment_methods.valid_from
        and orders.order_date <  payment_methods.valid_to

    group by payments.order_id

),
```

The inner join to `orders` is safe because `order_id` is unique there and the original left join from `orders` still keeps orders without payments. I used the order date because `raw_payments` has no payment date. If finance applies the rate by settlement date, use that instead.

After deploying, run `dbt build --full-refresh -s orders+`. **`daily_revenue` is incremental and only rebuilds the last three days**, so older days would keep the doubled revenue without a full refresh.

Expected result: credit card 871.00, `sum(amount)` 1672.00, order 1 at 10.00. I haven't run this, because the seed data wasn't provided. Check that fees now look sensible.

## 3. Tests

Add a grain test on the seed in `models/schema.yml`. With `dbt_utils` installed:

```yaml
  - name: raw_payment_methods
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [payment_method, effective_from]
```

Without it, a singular test works. This one would have caught the bug directly, since it compares `orders` to its source of truth:

`tests/assert_orders_reconcile_to_payments.sql`
```sql
-- fails if orders disagrees with stg_payments in total or per payment method
with o as (
    select
        sum(amount) as amount,
        sum(credit_card_amount) as credit_card,
        sum(coupon_amount) as coupon,
        sum(bank_transfer_amount) as bank_transfer,
        sum(gift_card_amount) as gift_card
    from {{ ref('orders') }}
),
p as (
    select
        sum(amount) as amount,
        sum(case when payment_method = 'credit_card' then amount end) as credit_card,
        sum(case when payment_method = 'coupon' then amount end) as coupon,
        sum(case when payment_method = 'bank_transfer' then amount end) as bank_transfer,
        sum(case when payment_method = 'gift_card' then amount end) as gift_card
    from {{ ref('stg_payments') }}
)
select *
from o cross join p
where abs(o.amount - p.amount) > 0.005
   or abs(o.credit_card - p.credit_card) > 0.005
   or abs(o.coupon - p.coupon) > 0.005
   or abs(o.bank_transfer - p.bank_transfer) > 0.005
   or abs(o.gift_card - p.gift_card) > 0.005
```

Also add a similar check that `sum(region_revenue.revenue)` equals `sum(stg_payments.amount)`. `raw_customer_regions` has an `updated_at` column, which suggests it can hold several rows per customer. That would cause the same fan-out in `region_revenue` as soon as a customer changes region. It currently ties out, but a `unique` test on `raw_customer_regions.customer_id` would be a cheap safeguard.
