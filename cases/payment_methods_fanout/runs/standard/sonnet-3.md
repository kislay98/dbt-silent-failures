claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 21 s | USD 0.088 | 2026-10-14 | project ad0b16b | prompt 037c69d41959

## 1. Root cause

`seeds/raw_payment_methods.csv` has **two rows for `bank_transfer`**: one with processor `anz` and one with `westpac`, both at fee rate 0.005. The seed is meant to have one row per payment method (its description says so), but nothing enforces that.

`models/orders.sql` (the `order_payments` CTE) does `payments left join payment_methods on payments.payment_method = payment_methods.payment_method`. Every bank transfer payment therefore matches two rows and is counted twice, while the other methods match one row each. This explains each symptom:

- **Bank transfer:** 822.00 in `orders` is exactly 2 × 411.00 on the bank statements.
- **Order total:** `sum(orders.amount)` is inflated by exactly the extra bank transfer amount, and 2083 − 1672 = 411.
- **Order 5:** 34.00 for a payment of 17.00.
- **Fees:** `fees` is also overstated for bank transfers, because the fan-out affects `sum(amount * fee_rate)` too.
- **Other methods:** credit card, coupon and gift card are correct because each has a single row.
- **Other models:** `region_revenue` and `customers` read `stg_payments` directly and never touch the seed, so they still total 1672.00.
- **`daily_revenue`:** it reads from `orders`, so it is polluted too. It shows 1696, which is neither 1672 nor 2083. That is consistent with it being incremental: only the last 3 days were rebuilt with the doubled amounts, and older days keep whatever the earlier runs produced. I can't confirm that from the data given.
- **Why every test passed:** the seed is only tested for `not_null`, and `orders.order_id` stays unique because the fan-out is collapsed by the `group by`. Nothing checks that `payment_method` is unique in the seed, or that `orders` reconciles to the payments.

## 2. Fix

**Data fix:** remove the duplicate `bank_transfer` row from the seed. Payments carry no processor column, so the `processor` field can't be used to choose between the two. If both processors are real, `raw_payments` needs a processor column and the join must use it. Either way, the join must stay one-to-one.

```csv
payment_method,processor,fee_rate
bank_transfer,anz,0.005
coupon,internal,0.0
credit_card,stripe,0.029
gift_card,internal,0.0
```

Then rebuild, using a full refresh so the stale `daily_revenue` history is recomputed:

```
dbt seed --select raw_payment_methods
dbt build --full-refresh
```

**Optional defensive change** in `orders.sql`, so a future duplicate can't inflate amounts silently. Fees would still be wrong if the duplicates had different rates, which is why the tests below matter:

```sql
payment_methods as (
    select payment_method, max(fee_rate) as fee_rate
    from {{ ref('raw_payment_methods') }}
    group by payment_method
),
```

## 3. Tests

Add a uniqueness test on the seed, in `schema.yml`:

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - not_null
          - unique
```

Add a reconciliation test, `tests/assert_orders_amount_matches_payments.sql`, which would have caught the symptom whatever the cause:

```sql
select
    (select sum(amount) from {{ ref('orders') }})       as orders_amount,
    (select sum(amount) from {{ ref('stg_payments') }}) as payments_amount
where abs(
    (select sum(amount) from {{ ref('orders') }})
  - (select sum(amount) from {{ ref('stg_payments') }})
) > 0.005
```

For per-order precision, join `orders` to `stg_payments` grouped by `order_id` and return any rows where the amounts differ. A similar check on `daily_revenue` against `orders` would also flag stale incremental history.

I haven't run any of this, since I only have the output you pasted. Please rerun `dbt build --full-refresh` and confirm that `sum(amount)` is 1672.00 and bank transfer is 411.00.
