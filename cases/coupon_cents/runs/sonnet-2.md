claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 31 s | USD 0.073 | 2026-10-14 | project bef6b69

## 1. Root cause

**`seeds/raw_payments.csv` (as loaded by the ingestion job) has mixed units, and `models/staging/stg_payments.sql` assumes one.**

`stg_payments.sql` does `amount / 100` on every row, on the stated assumption that `amount` is in cents. That holds for January and February and for early March, but the coupon rows from roughly order 76 (2018-03-20) onward are already in whole dollars. Dividing them by 100 shrinks them 100x.

The evidence comes from the output tables. I couldn't see the seed itself, so this is inferred rather than confirmed against the raw rows.

- **Marketing's ledger matches the raw values.** Taking the odd-looking coupon amounts as raw dollars gives March `18 + 2 + 2 + 23 = 45.00` and April `2 + 7 + 24 = 33.00`. Our totals of 18.27 and 0.33 are those sums with everything after the first 18 divided by 100. Order 58's 18.00 is a correct cents row, which is why the boundary falls between orders 58 and 76.
- **The settlement gap is the same error.** `622.00 - 595.27 = 26.73`, and `45.00 - 18.27 = 26.73`. The whole March discrepancy is the coupon shrinkage.
- **Mixed units show up within a single order.** Order 92 has `amount = 15.02`, made of a 15.00 non-coupon payment correctly converted from 1500 cents and a 0.02 coupon payment that was really $2.
- **April is off by the same mechanism.** `33.00 - 0.33 = 32.67`.

No test caught it because every test checks structure (unique, not_null, accepted_values) and none checks amounts or units.

## 2. Fix

Fix the data at the source rather than adding a date cutoff to the model. The ingestion job changed units for coupon rows partway through, and a `case when payment_date > ...` in staging would hide that and break again on the next change.

1. Find the affected rows. I can't see the seed, so confirm the boundary before changing anything:
   ```sql
   select id, order_id, amount
   from raw_payments
   where payment_method = 'coupon'
   order by id;
   ```
   The affected rows should be the coupon payments for orders 76, 81, 86, 92, 94 and 95, plus any later ones.
2. Multiply `amount` by 100 on those rows in `raw_payments.csv`, or fix the ingestion job so it emits cents consistently and reload.
3. Leave `stg_payments.sql` as is.
4. Run `dbt build`. March should show coupon 45.00 and amount 622.00, and April coupon 33.00.

Since `daily_revenue` is incremental and only rebuilds the last 3 days, run `dbt run --full-refresh -s daily_revenue` so March and April are corrected there too.

Separately, `raw_customer_regions` has an `updated_at` column but its tests only check `not_null`, not `unique` on `customer_id`. If it holds history rows, `region_revenue` will double-count. I haven't verified this, but it's worth checking.

## 3. Tests that would have caught it

A unit sanity check on staging, since sub-dollar payments are a strong sign of the wrong scale. Create `tests/assert_payment_amounts_look_like_dollars.sql`:

```sql
-- amounts are converted from cents; a positive value under $1 usually means
-- the source row was already in dollars and got divided by 100 twice
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount > 0 and amount < 1
```

This flags all six bad rows: 0.02, 0.02, 0.23, 0.02, 0.07 and 0.24.

A reconciliation test against finance's numbers catches any unit error that stays above $1. Load the marketing ledger and processor settlement as a seed, `seeds/finance_monthly_totals.csv` with columns `month, coupon_amount, settled_amount`, and add `tests/assert_orders_reconcile_to_finance.sql`:

```sql
with ours as (
    select
        date_trunc('month', order_date) as month,
        round(sum(coupon_amount), 2) as coupon_amount,
        round(sum(amount), 2) as settled_amount
    from {{ ref('orders') }}
    group by 1
)
select *
from ours
join {{ ref('finance_monthly_totals') }} f using (month)
where abs(ours.coupon_amount - f.coupon_amount) > 0.01
   or abs(ours.settled_amount - f.settled_amount) > 0.01
```

Before the fix, this returns the March and April rows and fails the build.
