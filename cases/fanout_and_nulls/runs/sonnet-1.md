claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 40 s | USD 0.091 | 2026-10-14 | project f769fdf+wk6ext | prompt 86aed168df9e

## 1. Root cause

Two separate defects stack up. The 2460 vs 1672 gap is +853 of double-counting and -65 of missing data (1607 + 65 = 1672).

**A. Fan-out in `models/orders.sql`, from a duplicated credit_card row in the `raw_payment_methods` seed.**

`orders` joins `stg_payments` to `raw_payment_methods` on `payment_method` alone, then computes `sum(payments.amount)` and the per-method sums over the joined rows. If the seed has two `credit_card` rows (for example one per processor), every credit card payment is counted twice.

The data fits this:
- `stg_payments` credit_card totals 853.00, and `orders.credit_card_amount` totals 1706.00, which is exactly 2×.
- Order 2 has one 20.00 payment and shows 40.00. Order 54 has 18.00 and shows 36.00.
- Bank transfer, coupon and gift card are not doubled (order 25 bank_transfer is 20.00 against a 20.00 payment), so only credit_card is duplicated.
- Order 2's fees of 1.16 equal 20 × (rate₁ + rate₂), so two rows matched.
- `orders.amount` totals 2460 = 1607 + 853, the doubled credit card total.
- `region_revenue` and `customers.customer_lifetime_value` don't join the seed, so they correctly show 1607.00.

The seed file isn't in what you pasted, so I'm inferring the duplicate from these numbers. Confirm it with `select payment_method, count(*) from raw_payment_methods group by 1`. The seed schema only tests `not_null`, with no `unique` on `payment_method`, so nothing flagged it.

**B. Ingestion loads NULL `amount` in `raw_payments`, and nothing tests for it.**

`stg_payments` has 8 rows with a NULL amount:
- bank_transfer: 3 rows
- coupon: 1 row
- gift_card: 1 row
- credit_card: 3 rows

`sum(...)` ignores NULLs, so these payments silently contribute 0. That explains the shortfalls against the processor statements:
- bank transfer: 25.00
- coupon: 16.00
- gift card: 6.00
- credit card: 871 − 853 = 18.00

Order 13 is an example. Its bank_transfer payment 15 is NULL, and the processor settled 19.00 = 5.00 + 14.00 missing.

The existing `not_null` tests on `orders.*_amount` can't catch this. `sum` over the NULL row still returns a value, so those tests pass.

## 2. Fix

**Seed fix (A):** remove the duplicate/stale `credit_card` row from `seeds/raw_payment_methods.csv`, keeping the row for the processor finance settles against. Then run `dbt seed -s raw_payment_methods`.

Optionally make the model robust too. Compute the amounts from `payments` alone and join the rates only for fees, so a bad dimension can only affect `fees`, not `amount`:

```sql
order_payments as (

    select
        order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payment_method = '{{ payment_method }}' then amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(amount) as total_amount

    from payments
    group by order_id

),

order_fees as (

    select
        payments.order_id,
        round(sum(payments.amount * coalesce(payment_methods.fee_rate, 0)), 2) as fees

    from payments
    left join payment_methods
        on payments.payment_method = payment_methods.payment_method
    group by payments.order_id

),
```

In `final`, join `order_payments` and `order_fees` on `order_id` and select `order_fees.fees`.

**Data fix (B):** the 8 NULL amounts need to be reloaded from the processor by the ingestion job. Query `select * from raw_payments where amount is null` to get the ids. dbt shouldn't invent these values. After the reload, the totals should be 1672.00 overall and 871.00 for credit card.

**Rebuild:** after both fixes, run `dbt build --full-refresh -s daily_revenue+ orders+` (or a full `dbt build --full-refresh`). `daily_revenue` only rebuilds the last three days, so older dates keep the doubled revenue until it is fully refreshed.

## 3. Tests that would have caught it

In `models/schema.yml`, under the `raw_payment_methods` seed (this catches A):

```yaml
      - name: payment_method
        tests:
          - not_null
          - unique
```

In `models/staging/schema.yml` (this catches B at the source):

```yaml
      - name: amount
        tests:
          - not_null
```

A reconciliation test in `tests/assert_orders_match_payments.sql` catches any fan-out or drop, whatever the cause:

```sql
with order_totals as (
    select order_id, amount from {{ ref('orders') }}
),

payment_totals as (
    select order_id, sum(amount) as amount
    from {{ ref('stg_payments') }}
    group by order_id
)

select
    order_totals.order_id,
    order_totals.amount as orders_amount,
    payment_totals.amount as payments_amount
from order_totals
join payment_totals using (order_id)
where abs(order_totals.amount - payment_totals.amount) > 0.005

union all

select null, o.total, p.total
from (select sum(amount) as total from {{ ref('orders') }}) o
cross join (select sum(amount) as total from {{ ref('stg_payments') }}) p
where abs(o.total - p.total) > 0.005
```

Today this returns order 2, 13 and others from the fan-out, plus the overall 2460 vs 1607 mismatch. The `not_null` test on `stg_payments.amount` fails on the 8 NULL rows. For a gap against the processor settlement itself, set `severity: warn` on a similar test against a loaded settlement table.
