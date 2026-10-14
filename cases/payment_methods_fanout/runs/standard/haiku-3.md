claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 29 s | USD 0.006 | 2026-10-15 | project ad0b16b | prompt 037c69d41959

## 1. Root cause

The bug is in the seed `raw_payment_methods` (inferred path `jaffle_shop/seeds/raw_payment_methods.csv`), which contains two rows for `bank_transfer`:

```
bank_transfer  anz       0.005
bank_transfer  westpac   0.005
```

The seed's own description says "one row per payment method", and `payment_method` has only a `not_null` test, not `unique`, so the duplicate passes.

`jaffle_shop/models/orders.sql` joins payments to this seed on `payment_method` and has no way to tell the two rows apart:

```sql
from payments
left join payment_methods
    on payments.payment_method = payment_methods.payment_method
```

Every bank transfer payment is duplicated before the `group by payments.order_id`. The `group by` collapses the rows back to one per order, so `order_id` stays unique, but the `sum(...)` expressions have already counted each bank transfer twice. The same fan-out affects `total_amount` (which becomes `amount`) and `fees`.

The numbers match this:

- Bank transfer: 822.00 is exactly 2 × 411.00.
- Order total: 2083.00 − 1672.00 = 411.00, the bank transfer total.
- Order 5: one 17.00 payment becomes 34.00.
- Credit card, coupon and gift card are unaffected because they only have one matching seed row.

`region_revenue` and `customers.customer_lifetime_value` total 1672.00 because they sum `stg_payments.amount` directly and never use `orders.amount` or the seed.

The dbt tests pass because none of them check values. `unique` and `not_null` on `orders` hold after the `group by`, and the `accepted_values` and `relationships` tests don't look at amounts. The per-method columns also still add up to `amount`, since both are doubled together.

A secondary issue: `daily_revenue` totals 1696.00, which matches neither 2083.00 nor 1672.00. It is incremental and only rebuilds the last three days, so older rows may be stale from an earlier build. I can't confirm the cause from the sample alone. See step 2.

## 2. Fix

**a) Fix the data.** Make `raw_payment_methods` one row per `payment_method`. Both rows have fee rate 0.005, so fees don't depend on which processor you keep. Finance should still confirm which processor applies to bank transfers, because it matters for reconciliation even though it doesn't change the amounts.

**b) Make the model immune to fan-out.** Aggregate the seed to one row per method before joining:

```sql
payment_methods as (

    {#-
    Processor and fee rate per payment method, loaded as a seed like the other raw tables
    #}
    select
        payment_method,
        max(fee_rate) as fee_rate
    from {{ ref('raw_payment_methods') }}
    group by payment_method

),
```

The `max` is only safe here because the two rates are equal. If they ever differ, the query should fail rather than pick one silently, so after (a) the simpler option is a plain `select *`. Either way, (a) is the real fix.

**c) Rebuild.** Run `dbt build --select raw_payment_methods+ --full-refresh`. The full refresh matters because `daily_revenue` won't correct older days otherwise. After rebuilding, check that `daily_revenue` sums to 1672.00.

## 3. A test that would have caught it

Two tests would have caught this, and both should be added:

**Uniqueness on the seed** (in `jaffle_shop/seeds/schema.yml` or `models/schema.yml`):

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - unique
          - not_null
```

**Reconciliation of orders against payments**, as a singular test in `jaffle_shop/tests/assert_order_amount_matches_payments.sql`:

```sql
select o.order_id, o.amount, p.paid
from {{ ref('orders') }} o
left join (
    select order_id, sum(amount) as paid
    from {{ ref('stg_payments') }}
    group by order_id
) p on o.order_id = p.order_id
where abs(coalesce(o.amount, 0) - coalesce(p.paid, 0)) > 0.005
```

This compares each order's amount to the payments source, which is the check finance is effectively doing. It fails on order 5 and on every bank transfer order. Adding a dbt unit test with a small fixture that includes a duplicated payment method would also catch the fan-out inside `orders.sql` before it reaches the warehouse.

I haven't run any of this. The conclusions come from the files and sample output provided, and the `daily_revenue` discrepancy is the one part I could not explain from what I have.
