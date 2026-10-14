claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 43 s | USD 0.008 | 2026-10-15 | project ad0b16b | prompt 037c69d41959

## 1. Root cause

**The data:** `raw_payment_methods` (the `raw_payment_methods` table, from the seed in `seeds/`, which I haven't seen) has two `bank_transfer` rows, one for `anz` and one for `westpac`, both at fee_rate 0.005. The other three methods have one row each. The file is still `seeds/raw_payment_methods.csv` as far as I can tell, but I'm inferring that from `orders.sql`'s `ref()` call, not from the file itself.

**The model:** `models/orders.sql`, in the `order_payments` CTE, left joins `payments` to `payment_methods` on `payment_method` before aggregating. Every bank-transfer payment therefore matches two rows and is counted twice in `sum(payments.amount)`. The `group by payments.order_id` hides the fan-out, so `unique` on `orders.order_id` still passes.

**The numbers confirm it:**
- Bank transfer doubles: 411 true → 822 shown. Total overstatement: 2083 − 1672 = 411, exactly the bank-transfer total.
- Order 5: 17.00 paid → 34.00 shown.
- Credit card, coupon, and gift card each have exactly one seed row, so they are not duplicated, which matches the statements.
- Fees are also doubled for bank transfer, because `fees` multiplies each duplicated row by 0.005.
- `customers.customer_lifetime_value` and `region_revenue` are correct because they never join `payment_methods`.

**Downstream:** `daily_revenue` totals 1696, which matches neither 1672 nor 2083. It is incremental and only rebuilds the last three days, so older rows were written under earlier data and are stale. A full refresh will fix them.

**Why the build was green:** The nightly log shows 35 nodes: 7 models and 28 tests. The 5 seeds and 4 seed tests (including `not_null` on `raw_payment_methods.payment_method`) did not run. There is also no `unique` test on that seed, so nothing would have flagged the duplicate.

## 2. Fix

**Data (primary):** Keep one row per `payment_method` in `raw_payment_methods`. Before deleting either bank-transfer row, ask finance or ops which processor is correct. The fee rate is the same for both, so the fees are unaffected either way. If bank transfers really do settle through two processors, the seed needs a different grain and `orders.sql` must aggregate it first. Given that `stg_payments` has one row per payment and the settlement counts each transfer once, I expect one row is correct.

Then rebuild, including the stale incremental model:

```
dbt seed --select raw_payment_methods
dbt build --full-refresh
```

Expected after the fix: `orders.amount` totals 1672.00, bank transfer totals 411.00 (Jan 136, Feb 74, Mar 162, Apr 39), and `daily_revenue` totals 1672.00.

If `raw_payment_methods` is actually loaded by the ingestion job rather than from a seed file, make the same deduplication upstream. The seed file isn't in the provided material, so I haven't verified this.

**Code (optional hardening):** The model should not silently accept a non-unique lookup. The unique test in section 3 gives you that without changing SQL.

## 3. Tests that would have caught this

**a) Unique key on the lookup (catches it at the source):** In `models/schema.yml`, under `seeds:`:

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests:
          - unique
          - not_null
```

This fails immediately on the current data. Make sure the seed tests actually run in the nightly job. They did not run in this log.

**b) Reconciliation against the source (catches the symptom in any model):** Add `tests/assert_orders_amount_matches_payments.sql`:

```sql
with orders_total as (
    select sum(amount) as total from {{ ref('orders') }}
),

payments_total as (
    select sum(amount) as total from {{ ref('stg_payments') }}
)

select *
from orders_total
cross join payments_total
where abs(orders_total.total - payments_total.total) > 0.005
```

This would have returned a row (2083 vs 1672). A per-method version (`bank_transfer_amount` against `stg_payments` where `payment_method = 'bank_transfer'`) would also catch it, and would point at the method. Note that the existing check that the four method columns sum to `amount` would not catch this, because both sides are doubled consistently.
