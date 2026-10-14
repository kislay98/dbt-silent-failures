claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 44 s | USD 0.007 | 2026-10-14 | project f769fdf+wk6ext | prompt 86aed168df9e

## 1. Root cause

There are two separate data problems. Together they explain every discrepancy.

**A. `orders.sql` fans out credit-card payments.** `raw_payment_methods` (the seed behind `models/orders.sql`) appears to have more than one row for `credit_card`. The model does:

```sql
payments left join payment_methods on payments.payment_method = payment_methods.payment_method
```

Each credit-card payment matches every `credit_card` row in the seed, so it is counted once per row. Every other method has one row, so it is counted once. The arithmetic fits:

- Payments total 1607 (`stg_payments`, excluding NULLs). `orders.amount` sums to 2460 = 1607 + 853, where 853 is the credit-card payments total.
- `credit_card_amount` is 1706 = 2 × 853.
- Order 2: one payment of 20.00 → `credit_card_amount` 40.00. Order 54: 18.00 → 36.00. Order 25: credit 22.00 → 44.00, bank transfer 20.00 unchanged.
- `fees` is also wrong. Credit-card fee rates are summed across both seed rows.

I can't see the seed file, so the duplicate row is inferred from the numbers. Confirm with:

```sql
select payment_method, processor, fee_rate from raw_payment_methods order by 1;
```

**B. Eight payments were loaded with a NULL `amount` in `raw_payments`.** `stg_payments` does `amount / 100`, which keeps NULL as NULL, and `sum()` silently skips it. Sample rows with NULL amounts: payment 15 (order 13, bank transfer), 21 (order 18, credit card), 30 (order 25, coupon), 62 (order 54, bank transfer), 67 (order 58, gift card). The NULL counts are 3 bank transfer, 1 coupon, 1 gift card and 3 credit card.

These missing amounts account exactly for the settlement gap:

| Method | Our payments | NULL rows | Processor | Gap |
|---|---|---|---|---|
| credit_card | 853 | 3 (18) | 871 | +18 |
| bank_transfer | 386 | 3 (25) | 411 | +25 |
| coupon | 169 | 1 (16) | 185 | +16 |
| gift_card | 199 | 1 (6) | 205 | +6 |

Total gap: 65 = 1672 − 1607. Order 13 settled 19.00 = 5.00 credit card + 14.00 bank transfer (NULL in our data).

Nothing in the tests catches the NULLs. `stg_payments.amount` has no `not_null` test, and the `not_null` tests on `orders` are on the pivoted columns, which are 0 or non-null after the fan-out.

`region_revenue` and `customers.customer_lifetime_value` total 1607 because they read `stg_payments` directly and never go through the fanned-out `orders.sql`. That is consistent with the diagnosis.

## 2. Fix

**A. Make the seed unique, then remove the duplicate.** Decide with finance which `credit_card` row (processor and fee rate) is correct, delete the other, and add a uniqueness test (section 3). Then rebuild.

If you want the model to be safe even when the seed is wrong, don't let the amount sums depend on the join. Compute `*_amount` and `total_amount` from `payments` alone, and join the seed only for `fees`, against a seed that is verified unique.

**B. Restore the missing amounts upstream.** Re-pull `raw_payments.amount` for the 8 NULL payment IDs from the processor, so they match the settlement. Do not `coalesce(amount, 0)` in staging, because that hides the same problem.

**C. Rebuild.** `daily_revenue` is incremental and only rebuilds the last 3 days, so older days keep the bad values. Run:

```
dbt build --full-refresh -s stg_payments+ raw_payment_methods+
```

## 3. Tests that would have caught this

Add to `models/schema.yml`:

```yaml
seeds:
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - not_null
          - unique   # would have failed on the duplicate credit_card row

models:
  - name: stg_payments   # in models/staging/schema.yml
```

In `models/staging/schema.yml`, under `stg_payments`:

```yaml
      - name: amount
        tests:
          - not_null   # would have failed on the 8 NULL amounts
```

Add a singular test, `tests/assert_order_amounts_reconcile_to_payments.sql`, to reconcile the two layers in total:

```sql
select 1
from (
    select
        (select sum(amount) from {{ ref('stg_payments') }}) as paid,
        (select sum(amount) from {{ ref('orders') }})       as ordered
)
where abs(paid - ordered) > 0.005
```

Any fan-out or dropped payment will make this return a row and fail the build. A per-order version (`orders.amount` equal to the sum of that order's payments) would also localise the failing order.

## Other things I noticed

- `region_revenue` joins `raw_customer_regions`, which has an `updated_at` column. If that feed ever has more than one row per customer, the same fan-out will inflate revenue there. Add a `unique` test on `customer_id`.
- `schema.yml` documents `customers.total_order_amount`, which the model doesn't produce. The docs are out of sync with the model.
