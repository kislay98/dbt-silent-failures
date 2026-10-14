claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 23 s | USD 0.070 | 2026-10-14 | project f769fdf+wk6ext | prompt 44b209d47f7f

## 1. Root cause

**`models/orders.sql`, `order_payments` CTE.** It joins `stg_payments` to `raw_payment_methods` on `payment_method` alone, to get `fee_rate`. `raw_payment_methods` is a rate history: its columns include `effective_from`, so a method can have several rows. The join assumes one row per method.

Credit card evidently has two rate rows, because only credit card is doubled and every method that matches its statement has one row. Each credit card payment is then duplicated by the join, and `sum(payments.amount)` and the `credit_card_amount` case-sum count it twice. The other methods have a single rate row, so they are unaffected.

The evidence fits this:
- Order 1: payment 1 is 10.00 in `stg_payments`, but `orders` shows 20.00.
- Order 13: credit card goes from 5.00 to 10.00, while bank transfer stays at 14.00, so `amount` is 24 instead of 19.
- Credit card: 871 on the statement, 1742 in `orders`, exactly double.
- Total: 2543 − 1672 = 871, which is the extra credit card amount.
- `region_revenue` and `customers` read `stg_payments` directly and never touch `raw_payment_methods`, so they still total 1672.

`fees` is also wrong for credit card payments. It is summed over the fanned-out rows, and it ignores which rate was in effect at the time.

All tests passed because nothing checks grain or reconciliation. The only checks on the seed are `not_null`. `orders` is still unique on `order_id` because of the `group by`, and the `not_null` tests can't see inflated values.

I haven't seen the seed contents. Confirm with:
```sql
select payment_method, count(*) from raw_payment_methods group by 1 having count(*) > 1
```

## 2. Fix

Take the amounts from `payments` only, and compute fees separately with an as-of join that picks one rate per payment: the latest rate effective on or before the order date. The `order_payments` CTE in `orders.sql` becomes:

```sql
payment_fees as (

    select
        payments.payment_id,
        payments.order_id,
        payments.amount * coalesce(payment_methods.fee_rate, 0) as fee

    from payments

    join orders
        on payments.order_id = orders.order_id

    left join payment_methods
        on payments.payment_method = payment_methods.payment_method
        and payment_methods.effective_from <= orders.order_date

    -- one rate per payment: the latest one in effect on the order date
    qualify row_number() over (
        partition by payments.payment_id
        order by payment_methods.effective_from desc
    ) = 1

),

order_payments as (

    select
        payments.order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payments.payment_method = '{{ payment_method }}' then payments.amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(payments.amount) as total_amount

    from payments

    group by payments.order_id

),

order_fees as (

    select
        order_id,
        round(sum(fee), 2) as fees

    from payment_fees

    group by order_id

),
```

In `final`, add `left join order_fees on orders.order_id = order_fees.order_id` and select `order_fees.fees`.

Some notes on the change:
- Amounts no longer depend on the seed at all.
- Fees will change for credit card orders. Confirm with finance that "rate at order date" is the intended rule. If you want the latest rate instead, drop the `effective_from` condition.
- `daily_revenue` is incremental and only rebuilds the last three days. Run `dbt build --full-refresh -s daily_revenue+` or it will keep the doubled history for older dates.
- `orders` has a left join, so orders with no payments get null `amount` and `credit_card_amount`. The `not_null` tests would catch that. Check whether such orders exist.
- `raw_customer_regions` has `updated_at`, which suggests it can also hold multiple rows per customer. `region_revenue` is correct today, but it would fan out the same way if a customer ever has two region rows. It's worth deduplicating there too.

## 3. Tests that would have caught it

Reconciliation, as a singular test, `tests/assert_orders_reconcile_to_payments.sql`. It returns rows on any mismatch, per method and in total:

```sql
with expected as (
    select
        sum(case when payment_method = 'credit_card' then amount end) as credit_card,
        sum(case when payment_method = 'coupon' then amount end) as coupon,
        sum(case when payment_method = 'bank_transfer' then amount end) as bank_transfer,
        sum(case when payment_method = 'gift_card' then amount end) as gift_card,
        sum(amount) as total
    from {{ ref('stg_payments') }}
),
actual as (
    select
        sum(credit_card_amount) as credit_card,
        sum(coupon_amount) as coupon,
        sum(bank_transfer_amount) as bank_transfer,
        sum(gift_card_amount) as gift_card,
        sum(amount) as total
    from {{ ref('orders') }}
)
select *
from expected, actual
where abs(expected.total - actual.total) > 0.005
   or abs(expected.credit_card - actual.credit_card) > 0.005
   or abs(expected.coupon - actual.coupon) > 0.005
   or abs(expected.bank_transfer - actual.bank_transfer) > 0.005
   or abs(expected.gift_card - actual.gift_card) > 0.005
```

This would have failed on the first run: 1742 against 871 for credit card.

Grain test on the seed, in `schema.yml`, which documents that `raw_payment_methods` is a history table (this uses `dbt_utils`):

```yaml
  - name: raw_payment_methods
    tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [payment_method, effective_from]
```

Together they check that the key is unique per effective date and that `orders` ties back to the payments it is built from.
