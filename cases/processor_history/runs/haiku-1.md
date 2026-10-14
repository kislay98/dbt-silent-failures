claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 48 s | USD 0.007 | 2026-10-14 | project f769fdf+wk6ext | prompt 44b209d47f7f

## 1. Root cause

**`jaffle_shop/models/orders.sql`, in the `order_payments` CTE, joins `payments` to `raw_payment_methods` on `payment_method` alone.** The seed (or raw table) `raw_payment_methods` has one row per payment method per rate period (it has an `effective_from` column), so the join fans out. `credit_card` appears to have two rows, and each credit-card payment is duplicated before the `sum(...)` calls run.

The evidence fits this exactly:

- **Only credit card is doubled.** `sum(amount)` is 2543.00, which is 1672.00 + 871.00, the credit-card total. Bank transfer (411), coupon (185) and gift card (205) each appear once.
- **Order 1:** one credit-card payment of 10.0 in `stg_payments`, shown as 20.0 in `orders`.
- **Order 13:** credit card 5.0 becomes 10.0. Bank transfer 14.0 stays 14.0, so the fan-out is per method, not per order.
- **`fees` is inflated the same way**, because it is computed over the same fanned-out rows.
- **`customers.customer_lifetime_value` and `region_revenue` stay at 1672.00** because they sum `payments.amount` without going through `order_payments`.

**Why the tests passed:**

- `unique` on `orders.order_id` still holds. The `group by payments.order_id` keeps the grain, but the sums inside it are inflated.
- `not_null` checks only for nulls, and no test reconciles `orders` back to `stg_payments`.
- The build log has no seed lines. It reports 32 data tests found and 28 run, and the 4 missing are exactly the seed tests (`raw_customer_regions` and `raw_payment_methods`). So the `raw_payment_methods` tests never ran nightly. Even if they had, `not_null` would not catch duplicate rows.

I have not seen the contents of `raw_payment_methods`, so the two credit-card rows are inferred from the arithmetic. Confirm with:

```sql
select payment_method, effective_from, fee_rate from raw_payment_methods order by 1, 2;
```

## 2. Fix

**Code change in `jaffle_shop/models/orders.sql`:** join each payment to the rate that was in effect on the order date, using non-overlapping date ranges.

```sql
{% set payment_methods = ['credit_card', 'coupon', 'bank_transfer', 'gift_card'] %}

with orders as (

    select * from {{ ref('stg_orders') }}

),

payments as (

    select * from {{ ref('stg_payments') }}

),

-- one row per payment method and rate period, valid from effective_from up to the next period
payment_method_rates as (

    select
        payment_method,
        fee_rate,
        effective_from,
        coalesce(
            lead(effective_from) over (partition by payment_method order by effective_from),
            date '9999-12-31'
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
        round(sum(payments.amount * coalesce(rates.fee_rate, 0)), 2) as fees

    from payments

    left join orders
        on payments.order_id = orders.order_id

    left join payment_method_rates as rates
        on payments.payment_method = rates.payment_method
        and orders.order_date >= rates.effective_from
        and orders.order_date < rates.effective_to

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
        order_payments.fees

    from orders

    left join order_payments
        on orders.order_id = order_payments.order_id

)

select * from final
```

Two assumptions to check:

- The rate is picked by the order date, since payments carry no timestamp.
- An order dated before the earliest `effective_from` finds no rate and gets a fee of 0 silently. If that can happen, set the earliest `effective_from` to an early date, or add a test that every payment finds a rate.

**Stored data:** `daily_revenue` is incremental and rebuilds only the last three days, so the doubled revenue for earlier dates will stay wrong after a normal run. Rebuild the downstream models with a full refresh:

```
dbt build --select orders+ --full-refresh
```

Also make sure the nightly job runs the seed tests (don't exclude `resource_type:seed`), or the `raw_payment_methods` tests will keep being skipped.

## 3. Test that would have caught it

Add a singular test that reconciles booked amounts per payment method against the payments feed. Save it as `jaffle_shop/tests/assert_orders_reconcile_to_payments.sql`:

```sql
with paid as (

    select payment_method, sum(amount) as paid_amount
    from {{ ref('stg_payments') }}
    group by payment_method

),

booked as (

    select 'credit_card' as payment_method, sum(credit_card_amount) as booked_amount from {{ ref('orders') }}
    union all
    select 'coupon', sum(coupon_amount) from {{ ref('orders') }}
    union all
    select 'bank_transfer', sum(bank_transfer_amount) from {{ ref('orders') }}
    union all
    select 'gift_card', sum(gift_card_amount) from {{ ref('orders') }}

)

select paid.payment_method, paid.paid_amount, booked.booked_amount
from paid
full outer join booked using (payment_method)
where abs(coalesce(paid_amount, 0) - coalesce(booked_amount, 0)) > 0.005
```

Against the current data this returns a `credit_card` row (1742.00 booked vs 871.00 paid) and fails the build. The `amount` total is covered because the four methods are exhaustive (the existing `accepted_values` test on `stg_payments` enforces that).

Also add a seed-level guard so the duplicate rate rows are caught at the source. Use a `dbt_utils.unique_combination_of_columns` test on `payment_method` and `effective_from` for `raw_payment_methods` if `dbt_utils` is installed. Otherwise a singular test that selects duplicate `(payment_method, effective_from)` pairs will do.
