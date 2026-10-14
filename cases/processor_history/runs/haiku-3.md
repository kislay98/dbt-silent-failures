claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 30 s | USD 0.005 | 2026-10-14 | project f769fdf+wk6ext | prompt 44b209d47f7f

## 1. Root cause

The fan-out is in `jaffle_shop/models/orders.sql`, in the `order_payments` CTE. It joins each payment to `raw_payment_methods` on `payment_method` alone:

```sql
from payments
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
```

`raw_payment_methods` has a `effective_from` column, so the processor feed evidently keeps more than one row per method, one per fee-rate period. The schema description ("one row per payment method") and the seed's shape disagree. Only `not_null` is tested on `payment_method`, so nothing enforces uniqueness.

Every credit card payment matches two rate rows and is emitted twice. The `sum(...)` calls then double-count it, which the numbers confirm:

- **Order 1:** credit card payment 10.00 → `credit_card_amount` 20.00. Fees 0.58 = 10 × 0.029 × 2.
- **Order 2:** 20.00 → 40.00. **Order 6:** 6.00 → 12.00. **Order 13:** card 5.00 → 10.00.
- **Bank transfers, coupons, gift cards** have no duplicate rate rows, so they are unchanged.
- **Totals:** card 871 × 2 = 1742, plus other methods 411 + 185 + 205 = 801, giving 2543 in `sum(orders.amount)`. The settlement is 871 + 801 = 1672.

`order_id` stays unique after the fan-out because the outer `group by payments.order_id` collapses the duplicates. That is why `unique_orders_order_id` and all the `not_null` tests pass.

`region_revenue` and `customers.customer_lifetime_value` read `payments` directly and never touch the rate join, so they still total 1672. That is correct.

I can't see the seed CSV, so confirm the duplicate with:

```sql
select payment_method, effective_from, fee_rate, count(*) from raw_payment_methods group by all order by 1, 2;
```

I expect two `credit_card` rows.

**Secondary effect:** `daily_revenue` sums `orders.amount`, so it is also overstated. It is incremental and only rebuilds the last three days, so historical days stay wrong until you full-refresh it.

## 2. Fix

Pick one fee-rate row per payment, using the rate effective on the order date. The date rule is my assumption about how the processor works, so confirm it with them. If the rate is simply the latest row, drop the date condition.

In `jaffle_shop/models/orders.sql`, add a `payment_rates` CTE before `order_payments`, and join to it instead of the raw seed:

```sql
payment_rates as (

    select
        payments.payment_id,
        payment_methods.fee_rate

    from payments

    join orders
        on payments.order_id = orders.order_id

    left join payment_methods
        on payments.payment_method = payment_methods.payment_method
        and payment_methods.effective_from <= orders.order_date

    -- one rate per payment: the most recent one effective on the order date
    qualify row_number() over (
        partition by payments.payment_id
        order by payment_methods.effective_from desc nulls last
    ) = 1

),

order_payments as (

    select
        payments.order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payments.payment_method = '{{ payment_method }}' then payments.amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(payments.amount) as total_amount,
        round(sum(payments.amount * coalesce(payment_rates.fee_rate, 0)), 2) as fees

    from payments

    left join payment_rates
        on payments.payment_id = payment_rates.payment_id

    group by payments.order_id

),
```

`payment_methods` must now be defined as the seed select (already is). Because `payment_rates` yields exactly one row per payment, the sums are correct.

Then rebuild the history, since `daily_revenue` only rebuilds the last three days:

```
dbt build --full-refresh -s daily_revenue+
dbt build -s orders+ 
```

Or simply `dbt build --full-refresh -s orders+ daily_revenue`. Also fix the seed itself: if the duplicate row is an error in the feed, remove it at source. Either way, the schema description for `raw_payment_methods` should say "one row per payment method per `effective_from`".

## 3. Tests that would have caught this

**a) Uniqueness on the seed key.** This is the test that would have flagged the duplicate at the source. In `jaffle_shop/models/schema.yml`, under `raw_payment_methods`:

```yaml
  - name: raw_payment_methods
    tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['payment_method', 'effective_from']
```

This needs the `dbt_utils` package. Without it, use a singular test (below) or `dbt_utils`-free SQL.

**b) Reconciliation against payments.** This catches the symptom at any layer, whatever the cause. Create `jaffle_shop/tests/assert_orders_amount_matches_payments.sql`:

```sql
with payment_total as (

    select sum(amount) as total from {{ ref('stg_payments') }}

),

order_total as (

    select sum(amount) as total from {{ ref('orders') }}

)

select *
from payment_total
join order_total
    on abs(payment_total.total - order_total.total) > 0.005
```

A singular test passes when it returns zero rows. Here it would have returned a row (2543 vs 1672) and failed the build. For per-method coverage, extend it to compare each `*_amount` column's sum against the matching `stg_payments` method total.
