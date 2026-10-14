claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 40 s | USD 0.160 | 2026-10-14 | project f769fdf+wk6ext | prompt deccfeb9fbe9

## 1. Root cause

**`raw_payment_methods` has two rows for `credit_card`, and `models/orders.sql` joins payments to it before aggregating, so every credit-card payment is counted twice.**

The schema tests describe `raw_payment_methods` as "one row per payment method". The loaded table has an `effective_from DATE` column, which makes it a rate history: each method gets a new row when its rate changes. In `orders.sql`, the `order_payments` CTE does:

```sql
from payments
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
group by payments.order_id
```

Each credit-card payment matches two `payment_methods` rows. Then `credit_card_amount`, `total_amount` and `fees` are all summed over the duplicated rows.

The numbers support this:

| | stg_payments | orders | |
|---|---|---|---|
| credit card | 871.00 | 1742.00 | ×2 |
| total | 1672.00 | 2543.00 | +871 = one extra copy of credit card |
| order 1 | 10.00 | 20.00 | ×2 |
| order 13 | 5 cc + 14 bt | 10 cc + 14 bt | only the cc part doubled |

- **Fees are also wrong.** Order 1 shows 0.58 on a real 10.00, so each credit-card payment is charged the sum of both rates (about 5.8%) instead of the rate in effect.
- **The other models match because they never touch this table.** `region_revenue` and `customers` read `stg_payments` directly. Bank transfer, coupon and gift card have only one row each.
- **`daily_revenue` is also inflated** because it reads `orders`. It is incremental and only rebuilds the last 3 days, so fixing `orders` alone will not repair older dates.

**Why no test failed:**
- The seed tests only check `not_null`; nothing checks the table's grain.
- Those seed tests did not run at all. The log says "Found … 32 data tests" but ran 28. The 4 tests on `raw_customer_regions` and `raw_payment_methods` were dropped along with the seeds, since the nightly build skips seeds because ingestion loads them.
- `raw_customer_regions.updated_at` has the same latent fan-out risk for `region_revenue`. It totals correctly today, so it has no duplicates yet.

## 2. Fix

Compute amounts from payments alone, and pick a single fee rate per payment: the latest one in effect on the order date. Replace the `order_payments` CTE in `models/orders.sql` with:

```sql
payment_fees as (

    select
        payments.payment_id,
        payments.order_id,
        payments.payment_method,
        payments.amount,
        coalesce(payment_methods.fee_rate, 0) as fee_rate

    from payments

    left join orders
        on payments.order_id = orders.order_id

    left join payment_methods
        on payments.payment_method = payment_methods.payment_method
        and payment_methods.effective_from <= orders.order_date

    -- raw_payment_methods keeps one row per rate change, take the rate in effect on the order date
    qualify row_number() over (
        partition by payments.payment_id
        order by payment_methods.effective_from desc
    ) = 1

),

order_payments as (

    select
        order_id,

        {% for payment_method in payment_methods -%}
        sum(case when payment_method = '{{ payment_method }}' then amount else 0 end) as {{ payment_method }}_amount,
        {% endfor -%}

        sum(amount) as total_amount,
        round(sum(amount * fee_rate), 2) as fees

    from payment_fees

    group by order_id

),
```

**Check the source rows before deploying:**

```sql
select * from raw_payment_methods where payment_method = 'credit_card'
```

- If the two rows have **different `effective_from` values**, the fix above is correct.
- If they have the **same date and different `processor` values**, the duplication is not a rate history. Finance needs to tell you which processor's rate applies; for example, add `processor` to `raw_payments` or deduplicate upstream.

Either way, the amounts will now be right, because nothing is fanned out before the sums.

**Then rebuild, including a full refresh of `daily_revenue`:**

```bash
dbt build --select orders+ --full-refresh
```

## 3. Tests that would have caught it

**A reconciliation test on the model.** This is a singular test, `tests/assert_orders_reconcile_to_payments.sql`. It runs in the nightly build no matter how seeds are handled:

```sql
-- every order's amount must equal the sum of its payments
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

join payments
    on orders.order_id = payments.order_id

where abs(orders.amount - payments.amount) > 0.005
```

This would have returned order 1 (20 vs 10) and every other credit-card order.

**A grain test on the lookup table.** In `models/schema.yml`, replace the "one row per payment method" assumption with the real key:

```yaml
  - name: raw_payment_methods
    description: One row per payment method and rate change from the processor feed
    tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['payment_method', 'effective_from']
```

This test only helps if it actually runs. Since ingestion loads these tables, declare them as `sources` rather than seeds referenced with `ref()`. Source tests are selected by `dbt build` even when seeds are excluded. Add the same kind of grain test to `raw_customer_regions` (`customer_id`, `updated_at`) before it causes the same problem in `region_revenue`.
