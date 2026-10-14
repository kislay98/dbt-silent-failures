claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 23 s | USD 0.068 | 2026-10-14 | project f769fdf+wk6ext | prompt 44b209d47f7f

## 1. Root cause

**File:** `models/orders.sql`, CTE `order_payments`, joined to the `raw_payment_methods` seed.

```sql
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
```

The seed has an `effective_from` column (see the `information_schema` output). `raw_payment_methods` is therefore a fee-rate history, with more than one row per `payment_method`. The model and its schema test assume one row per method. This is inferred from the column list, since the seed data wasn't included. Confirm it with `select payment_method, count(*) from raw_payment_methods group by 1`.

- **Credit card fans out.** Every credit-card payment matches 2 rate rows, so it is counted twice. Total is 1742 = 2 × 871, in every month, and order 1 shows 20.00 for a 10.00 payment.
- **Other methods are unaffected.** Bank transfer, coupon and gift card have a single row each, so they match the statements.
- **Totals are inflated.** `amount` is `sum(payments.amount)` over the fanned-out rows, so it is 2543 against 1672 (1672 + 871). `fees` is also wrong: each credit-card payment is charged both historical rates instead of the one in force.
- **Other models are fine.** `region_revenue` and `customers` read `stg_payments` directly and never touch the seed, so they still total 1672.
- **Why the tests passed.** `unique` on `order_id` still holds because the `group by` collapses the duplicates. The `not_null` tests can't see wrong values. The seed's own tests only check `not_null`, and nothing reconciles `orders` to payments.

## 2. Fix

Join each payment to the one rate in force on the order date. Build a validity window with `lead()`, and bring `orders` into `order_payments` for the date:

```sql
payment_methods as (

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

Check the assumptions before shipping:
- **Rate semantics.** I assumed `effective_from` means "this rate applies from that date until the next row".
- **Early orders.** An order dated before a method's first `effective_from` gets no rate, so its fees are 0 through the `coalesce`. Check whether that is acceptable.
- **Payments with no order.** The `inner join` drops payments whose order doesn't exist. `stg_payments` has no relationships test to `stg_orders`, so check this too. Total payments should still reconcile to 1672.

**Then run `dbt build --full-refresh -s daily_revenue+ orders+`** (at minimum `orders` and `daily_revenue`). `daily_revenue` is incremental and only rebuilds the last three days, so months of doubled revenue would stay in it after the `orders` fix. Run it without `--full-refresh` and it will still be wrong.

Also add a guard on the seed so a second row can't silently reappear. Declare `effective_from` as not_null in the seed's schema, and test uniqueness of `(payment_method, effective_from)`.

## 3. Tests that would have caught it

**Singular test** `tests/assert_orders_reconcile_to_payments.sql`. It returns rows on failure, so any fan-out fails the build:

```sql
with p as (
    select order_id, sum(amount) as amount,
           sum(case when payment_method = 'credit_card' then amount else 0 end) as credit_card_amount
    from {{ ref('stg_payments') }}
    group by order_id
)
select o.order_id, o.amount, p.amount as expected, o.credit_card_amount, p.credit_card_amount as expected_cc
from {{ ref('orders') }} o
join p using (order_id)
where abs(o.amount - p.amount) > 0.005
   or abs(o.credit_card_amount - p.credit_card_amount) > 0.005
```

Add a total-level check too, `sum(orders.amount) = sum(stg_payments.amount)` (1672 against 2543 here). Alternatively, use `dbt_utils.equality` or `dbt_utils.equal_rowcount` on a pre-aggregated join.

For the root cause itself, add `dbt_utils.unique_combination_of_columns` on `raw_payment_methods` over `(payment_method, effective_from)`. Combine that with a singular test that asserts the rate-window join returns at most one rate per payment. The reconciliation test above is the one that fails today.
