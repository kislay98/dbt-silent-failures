claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 21 s | USD 0.087 | 2026-10-14 | project ad0b16b | prompt 037c69d41959

## 1. Root cause

`raw_payment_methods` (the seed feeding `models/orders.sql`) has two rows for `bank_transfer`, one for `anz` and one for `westpac`, both at 0.005. The seed is declared one row per payment method, but nothing enforces that.

In `orders.sql`, the `order_payments` CTE does `payments left join payment_methods on payments.payment_method = payment_methods.payment_method`. Each bank transfer payment therefore matches two rows and is counted twice. Credit card, coupon and gift card each match one row, so they stay correct. That explains the symptoms:

- `bank_transfer_amount` is exactly 2x (822 vs 411), and `sum(orders.amount)` is 411 too high (2083 vs 1672). Order 5's 17.00 shows as 34.00.
- `fees` on bank transfers is also doubled.
- `region_revenue` and `customers.customer_lifetime_value` read `stg_payments` directly and never touch the seed, so they are right at 1672.
- All tests pass because `orders.order_id` stays unique (the double count happens inside the aggregate, before the final join) and the seed is only tested for `not_null`.

A second problem is hidden in `daily_revenue`. It sums `orders.amount`, but it is incremental and rebuilds only the last 3 days. Its monthly totals are Jan 496, Feb 415, Mar 622, Apr 163, which is 1696 overall. Jan to Mar match the correct figures (632 − 272/2, and so on), so those days were built before the duplicate row appeared. April matches neither (178 total, correct is 139, shown 163), so only its last three days were rebuilt with doubled bank transfers. `daily_revenue` is therefore a mix of correct and inflated days, and it will stay wrong until it is fully refreshed.

## 2. Fix

1. **Data:** make `seeds/raw_payment_methods.csv` one row per `payment_method`. Which `bank_transfer` row to keep is a call for whoever owns the feed, since the processors differ. The `payments` table has no processor column, so payments can't be attributed to `anz` or `westpac` anyway. The fee rate is identical, so the output is the same either way.

2. **Model:** make the join safe against future duplicates in `orders.sql`:

```sql
payment_methods as (

    select
        payment_method,
        max(fee_rate) as fee_rate
    from {{ ref('raw_payment_methods') }}
    group by payment_method

),
```

   Use `max` only if differing rates per method can't occur. The uniqueness test below guards that.

3. **Rebuild the incremental model:** the 3-day window won't repair the older days.

```
dbt build --full-refresh -s orders daily_revenue
```

After this, `sum(orders.amount)` should be 1672.00, bank transfer 411.00 and `daily_revenue` should total 1672.00.

## 3. Tests

In `models/schema.yml`, under the `raw_payment_methods` seed:

```yaml
      - name: payment_method
        tests:
          - not_null
          - unique
```

That alone fails on the duplicate. Add a reconciliation test, `tests/assert_orders_amount_matches_payments.sql`, to catch any fan-out regardless of cause:

```sql
select
    (select sum(amount) from {{ ref('orders') }}) as orders_total,
    (select sum(amount) from {{ ref('stg_payments') }}) as payments_total
where abs(
    (select sum(amount) from {{ ref('orders') }})
  - (select sum(amount) from {{ ref('stg_payments') }})
) > 0.005
```

A similar check can compare `sum(revenue)` in `daily_revenue` with `sum(amount)` in `orders`, to catch the stale incremental state.
