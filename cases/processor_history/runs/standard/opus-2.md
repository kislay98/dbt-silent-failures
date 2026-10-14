claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 34 s | USD 0.194 | 2026-10-15 | project ad0b16b | prompt b0b450385f10

# Credit card payments double-counted by a fan-out join in `orders.sql`

## 1. Root cause

The bug is in **`models/orders.sql`**, and it was triggered by a new row in **`raw_payment_methods`**.

`raw_payment_methods` now holds two `credit_card` rows. The card processor changed from Stripe to Adyen on 2018-03-01, so the table is history with one row per method per `effective_from` date:

| payment_method | processor | fee_rate | effective_from |
|---|---|---|---|
| credit_card | stripe | 0.029 | 2018-01-01 |
| credit_card | adyen | 0.029 | 2018-03-01 |

In `order_payments`, `orders.sql` joins on the method only:

```sql
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
```

That join matches every credit card payment to both rows, so each one is summed twice. The numbers line up exactly:

- **Credit card total:** 1742 = 2 × 871, and it doubles in every month because the join ignores dates.
- **Grand total:** 2543 − 1672 = 871, which is exactly one extra copy of the credit card total.
- **Order 1:** a 10.00 card payment shows as 20.00.
- **Other methods:** coupon, bank transfer and gift card have one row each, so they are correct.
- **`fees`:** also doubled for card payments, though nobody has flagged it yet.
- **`customers` and `region_revenue`:** read `stg_payments` directly and never join `raw_payment_methods`, so they still show 1672.
- **`daily_revenue`:** contaminated where it was rebuilt. It reads `orders`, and only the trailing days are rebuilt each run. January to March (496 / 415 / 622) still hold the old correct values, but April shows 163 against a correct 139 (163 − 48/2).

**Why the tests passed:**
- `orders` only has `not_null` tests, and none of them check that totals reconcile.
- Nothing asserts a grain on `raw_payment_methods`.
- The 4 seed tests that do exist are not running. The log shows 32 tests found but only 28 executed, because seeds are excluded from the nightly build.

## 2. Fix

The data is valid: the processor really did change. The fix belongs in the code, which should pick the rate that was in effect on the order date. In `models/orders.sql`:

```sql
payment_methods as (

    {#-
    Processor and fee rate per payment method, one row per method per effective_from date
    #}
    select
        *,
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

Notes:
- The `final` CTE is unchanged.
- `payments` has no date of its own, so the order date stands in for the payment date. Use a payment timestamp instead if ingestion can provide one.

Then rebuild `daily_revenue` from scratch. Its incremental window would only fix the last 3 days, so earlier April days would stay wrong:

```
dbt build -s orders+ --full-refresh
```

## 3. Tests that would have caught it

**Reconciliation test (catches any fan-out, whatever its cause).** Add `tests/assert_orders_reconcile_to_payments.sql`:

```sql
-- every order's amount must equal the sum of its payments; a fan-out join inflates orders.amount
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

left join payments
    on orders.order_id = payments.order_id

where coalesce(orders.amount, 0) <> coalesce(payments.amount, 0)
```

On the current data, this would fail on every order that has a card payment, starting with order 1 (20 against 10).

**Grain test on the lookup table.** Assert that `(payment_method, effective_from)` is unique on `raw_payment_methods`. In `models/schema.yml`:

```yaml
  - name: raw_payment_methods
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['payment_method', 'effective_from']
```

You can also use a singular test grouping on those two columns with `having count(*) > 1`.

**Make seed tests run.** Since ingestion loads the raw tables, declare them as dbt `sources` rather than seeds. That way their tests run in the nightly `dbt build` instead of being silently dropped with the excluded seeds.
