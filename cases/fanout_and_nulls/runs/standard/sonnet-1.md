claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 28 s | USD 0.098 | 2026-10-14 | project ad0b16b | prompt 1db8a43ee8e9

## 1. Root cause

There are two separate data problems. The first is a fan-out, the second is missing amounts. Neither is caught by the existing tests.

**A. `seeds/raw_payment_methods.csv` has `credit_card` twice (stripe and adyen), and `models/orders.sql` assumes one row per method.**
- `order_payments` does `payments left join payment_methods on payment_method`, so every credit-card payment matches two rows and is counted twice. Bank transfer, coupon and gift card are unaffected.
- That is why order 2 (charged 20.00) shows 40.00. `credit_card_amount`, `amount` and `fees` are all inflated.
- The only uniqueness test on that seed is `not_null`, so the duplicate key passes. `orders.order_id` stays unique because the fan-out is collapsed again by `group by`.

**B. `raw_payments.amount` is NULL on some rows (ingestion job).**
- Examples are payment 30 (coupon, order 25) and payment 55 (credit card, order 49).
- `sum()` skips NULLs, and `case ... else 0` hides them, so the `not_null` tests on `orders.*_amount` pass trivially. `stg_payments.amount` has no `not_null` test.
- `region_revenue` and `customers` read `stg_payments` directly, so they have no fan-out. Their 1,607.00 is the true 1,672.00 minus the NULL rows: 1,672 − 1,607 = 65.00.
- The shortfalls match: 18 (credit card) + 25 (bank) + 16 (coupon) + 6 (gift) = 65.
- Order 13 fits the same pattern: its 5.00 credit-card payment is doubled to 10.00, and the rest of its 19.00 settlement is a NULL-amount payment. I couldn't check this directly, since the seed rows aren't in what you pasted.

**Check on the totals:** the credit-card excess is 1,706 − 871 = 835, and 835 − 47 (NULL bank, coupon and gift amounts) = 788, which equals 2,460 − 1,672. The numbers reconcile.

**C. `daily_revenue` is also stale.**
- Its monthly totals (496, 415, 622 and 163) don't match `orders` (668, 679, 950 and 163). Only April, the last three days, was rebuilt.
- The incremental model only rebuilds the last 3 days, so earlier dates keep whatever the earlier runs produced. Fixes to older orders never reach it.

## 2. Fix

1. **Dedupe the method dimension.** Both credit-card rows have the same 2.9% rate, and `raw_payments` has no processor column, so the model can't attribute a payment to a processor anyway. In `orders.sql`:
   ```sql
   payment_methods as (
       select payment_method, max(fee_rate) as fee_rate
       from {{ ref('raw_payment_methods') }}
       group by payment_method
   ),
   ```
   Alternatively, drop the duplicate row from the seed if only one processor is live. If the processors really have different rates, `raw_payments` needs a processor column to join on.
2. **Restore the NULL amounts at the source.** Re-ingest the 65.00 of NULL-amount `raw_payments` rows from the processor statements. dbt can't invent these. Until then, make the staging model fail loudly (see the tests below).
3. **Rebuild `daily_revenue`:** `dbt build --full-refresh -s daily_revenue+`. To stop this recurring, widen the lookback window or drop the incremental config. At 69 rows, a plain table is cheaper and always correct.

## 3. Tests that would have caught it

```yaml
# seeds / models schema.yml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests: [not_null, unique]

  - name: stg_payments
    columns:
      - name: amount
        tests: [not_null]
```

A reconciliation test, `tests/assert_orders_amount_matches_payments.sql`, catches the fan-out even if the join changes later:
```sql
select
    (select sum(amount) from {{ ref('orders') }})        as orders_total,
    (select sum(amount) from {{ ref('stg_payments') }})  as payments_total
where abs(
    (select sum(amount) from {{ ref('orders') }})
  - (select sum(amount) from {{ ref('stg_payments') }})
) > 0.005
```

A per-order version is stronger, because totals can cancel out:
```sql
select o.order_id
from {{ ref('orders') }} o
join (select order_id, sum(amount) as amt from {{ ref('stg_payments') }} group by 1) p
  using (order_id)
where abs(o.amount - p.amt) > 0.005
```

The `unique` test on `raw_payment_methods` fails on the duplicated `credit_card` row, and the `not_null` test on `stg_payments.amount` fails on the 65.00 of missing amounts. After the fixes, `orders.amount`, `region_revenue` and `customers.customer_lifetime_value` should all total 1,672.00.
