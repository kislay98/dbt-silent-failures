claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 30 s | USD 0.098 | 2026-10-14 | project ad0b16b | prompt 1db8a43ee8e9

## 1. Root cause

There are two defects. The first is code-side and explains most of the gap.

**A. `seeds/raw_payment_methods` has `credit_card` twice (stripe and adyen), and `models/orders.sql` joins on `payment_method` alone.**
- The `left join payment_methods on payments.payment_method = payment_methods.payment_method` matches two rows for every credit card payment, so each is counted twice.
- The `credit_card_amount`, `amount` and `fees` columns are all inflated. Order 2 shows 40 against the 20 charged.
- Bank transfer, coupon and gift card each match one row, so they are unaffected by the fan-out.
- Nothing caught it because the tests only check `unique` and `not_null` on `orders`, which stays one row per order after the `group by`. No test covers `raw_payment_methods`, and none reconciles totals.

**B. `raw_payments.amount` contains NULLs, for example payments 30 (coupon) and 55 (credit card).**
- `sum()` skips them silently, and `case ... else 0` plus `not_null` on `orders.amount` hide it.
- This explains why bank transfer, coupon and gift card are short by 25, 16 and 6 against the processor.
- `stg_payments` has no `not_null` test on `amount`, and the `not_null` tests sit on `orders`, where they cannot fail.

The numbers reconcile:
- `region_revenue` and `customers` read `stg_payments` directly, with no join to the methods seed. Their 1607 is therefore the true total minus the NULL payments.
- 1672 − 1607 = 65, which is 25 + 16 + 6 for the three affected methods plus 18 for credit card.
- Credit card in `orders` is 2 × 853 = 1706, and 871 − 18 = 853. The 18 are NULL credit card amounts.
- Orders total: 1607 + 853 (the duplicated extra) = 2460.

I can't see the seed rows, so I haven't confirmed the NULL payments order by order (order 13's 19 vs 10 fits this pattern). The queries in section 3 will list them.

**Secondary issue: `daily_revenue` is stale.** Its months total 1696 against 2460 for `orders`.
- On each run it only rebuilds the last 3 days, so older rows keep values from earlier runs, before the duplicate was added and before payments were corrected.
- Only April matches `orders`.

## 2. Fix

**Dedupe the methods in `orders.sql`.** This is safe here because both credit card rows have the same fee rate. If the rates ever differ, `payments` needs a processor column.

```sql
payment_methods as (

    select payment_method, max(fee_rate) as fee_rate
    from {{ ref('raw_payment_methods') }}
    group by payment_method

),
```

**Data fixes:**
- Have the ingestion job reload the NULL `amount` rows for credit card, coupon, bank transfer and gift card from the processor feed. Don't coalesce them to 0.
- Decide whether `raw_payment_methods` should keep one row per method. If so, drop the adyen row or add a processor column to `raw_payments`.
- Run `dbt build --full-refresh -s daily_revenue+` so the stale history is rebuilt. A cheap guard is to widen the lookback, or to full-refresh on a schedule.

After the dedupe, the total should be 1607 until the NULL payments are reloaded, and 1672 after.

## 3. Tests

In `models/schema.yml`, under the seed and staging models:

```yaml
  - name: raw_payment_methods
    columns:
      - name: payment_method
        tests: [not_null, unique]   # fails today: credit_card x2
```
```yaml
  - name: stg_payments
    columns:
      - name: amount
        tests: [not_null]           # fails today: NULL amounts
```

Add a singular test, `tests/assert_orders_reconcile_to_payments.sql`:

```sql
select o.total, p.total
from (select sum(amount) as total from {{ ref('orders') }}) o
cross join (select sum(amount) as total from {{ ref('stg_payments') }}) p
where abs(o.total - p.total) > 0.005
```

Add an equivalent check that `region_revenue` and `customers.customer_lifetime_value` sum to the same total. To list the offending NULL payments:

```sql
select * from raw_payments where amount is null
```
