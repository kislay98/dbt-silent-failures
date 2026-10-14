claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 24 s | USD 0.069 | 2026-10-14 | project f769fdf+wk6ext | prompt b430ab6cd05c

## 1. Root cause

**`models/orders.sql` joins to `raw_payment_methods`, and that seed has more than one row for `bank_transfer`.** The seed file itself isn't in what you pasted, so I can't tell which row is the bad one.

The `order_payments` CTE does `payments left join payment_methods on payment_method`, then sums `payments.amount`. If a method appears twice in the seed, every payment of that method is duplicated before the sum, so its amount is counted twice.

The evidence points to this:
- **Only `bank_transfer` is doubled.** `stg_payments` has 411.00 of bank transfers, and `orders` has 822.00. 2083 − 1672 = 411, which is exactly the extra bank transfer total.
- **Order 5** has one 17.00 bank-transfer payment but shows 34.00. Order 13 has 14.00 shown as 28.00, and order 25 has 20.00 shown as 40.00. The credit card and coupon amounts on the same orders are untouched.
- **`region_revenue` and `customers`** read `stg_payments` directly and never touch the seed, so they still total 1672.00.
- **All 32 data tests passed** because none checks the seed's grain or reconciles `orders` to payments. `not_null` on `fee_rate` can't see duplicates.
- **`fees` is probably wrong for bank transfers too.** It sums `amount * fee_rate` across both seed rows, so it applies both rates.

To confirm:
```sql
select payment_method, count(*), list(processor), list(fee_rate)
from raw_payment_methods group by 1 having count(*) > 1;
```

## 2. Fix

1. **Data:** In `seeds/raw_payment_methods.csv`, keep one row per `payment_method`. This is a business decision: either a stale or duplicate row from the processor feed, or a second processor that needs a rule for which payments it applies to. `raw_payment_methods` has no effective-date column to pick by.
2. **Model hardening in `orders.sql`:** Make the amounts independent of the fee join, so a bad dimension can't inflate them again:
```sql
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
        payments.order_id,
        round(sum(payments.amount * coalesce(payment_methods.fee_rate, 0)), 2) as fees
    from payments
    left join payment_methods
        on payments.payment_method = payment_methods.payment_method
    group by payments.order_id

),
```
   Then join `order_fees` in `final` and select `order_fees.fees`. A duplicate seed row would then only distort `fees`, and the grain test below would catch that.
3. **Rebuild with `dbt build --full-refresh`.** `daily_revenue` only rebuilds the last 3 days, so older dates keep the doubled revenue unless you full-refresh it.

## 3. Tests

Add a uniqueness test on the seed in `models/schema.yml`, under `raw_payment_methods` → `payment_method`:
```yaml
      - name: payment_method
        tests:
          - not_null
          - unique
```
Add a reconciliation singular test, `tests/assert_orders_amount_matches_payments.sql`. It returns rows only on a mismatch, so it fails the build:
```sql
with o as (select sum(amount) as total from {{ ref('orders') }}),
     p as (select sum(amount) as total from {{ ref('stg_payments') }})
select o.total as orders_total, p.total as payments_total
from o cross join p
where abs(o.total - p.total) > 0.005
```
Before the fix, this fails with 2083.00 vs 1672.00. A per-method version of the same check, comparing `sum(bank_transfer_amount)` with the `stg_payments` total, would also point straight at the method.

`raw_customer_regions` has an `updated_at` column and only a `not_null` test. It matches today, but it is exposed to the same kind of fan-out, so add `unique` on `customer_id` there too.
