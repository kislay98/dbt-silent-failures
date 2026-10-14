claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 28 s | USD 0.134 | 2026-10-14 | project f769fdf+wk6ext | prompt deccfeb9fbe9

# dbt fan-out: `credit_card` payments counted twice in `orders`

## 1. Root cause

The bug is in `models/orders.sql`, in the `order_payments` CTE. It joins `stg_payments` to `raw_payment_methods` on `payment_method` alone. That assumes the table has one row per payment method, as its description says. It doesn't: the loaded table has an `effective_from` column, so it is a versioned fee table. `credit_card` now has two rows, probably because its fee rate changed. Every credit card payment therefore matches two rows and is summed twice in `credit_card_amount`, `amount` and `fees`.

The numbers match this exactly:

| Check | Expected | In `orders` |
|---|---|---|
| Credit card total | 871.00 in `stg_payments` | 1742.00 (2 × 871) |
| Grand total | 1672.00 | 2543.00 (1672 + 871) |
| Order 1 | one 10.00 card payment | 20.00 |
| Order 13 | 5.00 card + 14.00 bank transfer | 10.00 card + 14.00 bank = 24.00 |

- Bank transfer, coupon and gift card still have one row each, so they reconcile.
- `fees` is wrong too. Order 1 shows 0.58 on a 10.00 payment, meaning the old and new card rates were both applied (≈0.058 combined).
- `region_revenue` and `customers` read `stg_payments` without this join, so they still total 1672.00.
- `daily_revenue` is built from `orders`, so it is inflated as well. Because it is incremental, only the last 3 days are rebuilt on each run, so it needs a `--full-refresh` after the fix.

**Why no test failed:**
- No test checks grain or reconciliation. `unique(order_id)` and `not_null` still pass after a fan-out inside a `sum`.
- The seed tests never run. The log shows "32 data tests found", but only 28 ran. The 4 tests on `raw_payment_methods` and `raw_customer_regions` are skipped because the raw tables are loaded by ingestion and seeds are excluded from the nightly build. So even a `unique` test on `payment_method` would not have fired.

## 2. Fix

Join each payment to the rate that was in effect on its order date. In `models/orders.sql`, replace the `payment_methods` and `order_payments` CTEs:

```sql
payment_methods as (

    {#-
    Processor and fee rate per payment method, one row per rate change (effective_from)
    #}
    select
        payment_method,
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

Then run `dbt build --full-refresh -s orders+` so `daily_revenue` is rebuilt as well.

Two caveats:
- `stg_payments` has no payment date, so I used the order date to pick the rate. Confirm with finance that this is the right date for the fee.
- Orders dated before the earliest `effective_from` will get 0 fees through the `coalesce`. The `fees` test below would catch that.

## 3. Tests that would have caught it

Put them on models, because those tests actually run in the nightly build.

**(a) Reconciliation test.** It fails on any fan-out, from any cause. Save as `tests/assert_orders_amount_matches_payments.sql`:

```sql
-- every order's amount must equal the sum of its payments in staging
with payments as (
    select order_id, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by order_id
)

select orders.order_id, orders.amount, payments.amount as payments_amount
from {{ ref('orders') }} as orders
left join payments on orders.order_id = payments.order_id
where abs(coalesce(orders.amount, 0) - coalesce(payments.amount, 0)) > 0.001
```

This test would have returned orders 1, 2, 6, 13, 99 and every other order with a card payment.

**(b) Grain test on the rate table.** Declare `raw_payment_methods` (and `raw_customer_regions`) as **sources**, since they come from ingestion, not seeds. Then add a test that `payment_method, effective_from` is unique, for example `dbt_utils.unique_combination_of_columns`, and run `dbt source freshness` / `dbt test -s source:*` in the nightly job.

Also check `raw_customer_regions`: it has an `updated_at` column, so it may be versioned too. `region_revenue` joins on `customer_id` alone and would fan out the same way the first time a customer changes region. Today it still totals 1672.00, but the same pattern is waiting to fail there.
