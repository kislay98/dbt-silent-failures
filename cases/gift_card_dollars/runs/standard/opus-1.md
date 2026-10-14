claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 33 s | USD 0.245 | 2026-10-14 | project ad0b16b | prompt e23be30fc7e7

# Gift card payments are in dollars, not cents

## 1. Root cause

**File:** `jaffle_shop/seeds/raw_payments.csv`, all 12 `gift_card` rows.

Every other payment method in `raw_payments` is recorded in cents. The `gift_card` rows were loaded in whole dollars:

| id | order_id | amount (as loaded) | should be |
|---|---|---|---|
| 9 | 9 | 23 | 2300 |
| 22 | 19 | 6 | 600 |
| 26 | 23 | 23 | 2300 |
| 48 | 43 | 18 | 1800 |
| 49 | 44 | 11 | 1100 |
| 67 | 58 | 6 | 600 |
| 68 | 59 | 28 | 2800 |
| 71 | 62 | 14 | 1400 |
| 92 | 80 | 3 | 300 |
| 99 | 87 | 30 | 3000 |
| 107 | 93 | 26 | 2600 |
| 110 | 96 | 17 | 1700 |

`stg_payments.sql` divides every row by 100 (`amount / 100 as amount`), so these become 0.23, 0.06 and so on.

The numbers reconcile exactly:
- The gift card rows sum to 205, which matches the provider statement (205.00). Divided by 100, they give the 2.05 you see.
- 1469.05 − 2.05 + 205.00 = **1672.00**, which matches the processor settlement total.
- Order 9 has `amount` 23 in raw, so it shows 0.23 instead of 23.00.

Fees are unaffected because `gift_card` has `fee_rate` 0.

None of the 32 tests can catch this. They only check `not_null`, `unique`, `accepted_values` and `relationships`. Nothing checks magnitude or reconciles totals.

**Secondary issue:** `daily_revenue` is now inconsistent with `orders`. Its January revenue is 496.00, while `orders` gives 444.52. That 496.00 equals 444.52 − 0.52 + 52, which is the correct value. So `daily_revenue` still holds dates computed on an earlier run, when gift card amounts were correct. Only the last 3 days (≥ 2018‑04‑06) were rebuilt with the bad data. That is why April is 122.17, a mix of 26.00 for order 93 (old) and 0.17 for order 96 (new). After the data fix, this incremental model needs a full refresh. Otherwise it stays partly stale.

## 2. Fix

Correct the 12 seed rows to cents (multiply `amount` by 100), keeping the contract that `raw_payments.amount` is always in cents:

```csv
9,9,gift_card,2300
22,19,gift_card,600
26,23,gift_card,2300
48,43,gift_card,1800
49,44,gift_card,1100
67,58,gift_card,600
68,59,gift_card,2800
71,62,gift_card,1400
92,80,gift_card,300
99,87,gift_card,3000
107,93,gift_card,2600
110,96,gift_card,1700
```

Then rebuild, including a full refresh of the incremental model:

```bash
dbt build --full-refresh
```

The alternative is a staging rule like `case when payment_method = 'gift_card' then amount else amount / 100 end`. Only use that if the upstream gift card feed is *contractually* in dollars. Otherwise it hides a feed bug. Either way, find out why the gift card rows arrived in dollars, so the next load doesn't repeat it.

Expected results after the fix: `sum(orders.amount)` = 1672.00, `sum(gift_card_amount)` = 205.00, order 9 `amount` = 23.00, and `daily_revenue` totals equal `orders` totals.

## 3. Tests that would have caught it

**a) Reconciliation against the external statements (primary).** This is the check finance did by hand. Add a seed `seeds/payment_control_totals.csv`:

```csv
payment_method,expected_total
gift_card,205.00
```

Then add a singular test, `tests/assert_payments_reconcile_to_statements.sql`:

```sql
-- fails if any payment method's total doesn't match its provider statement
with actual as (
    select payment_method, sum(amount) as actual_total
    from {{ ref('stg_payments') }}
    group by payment_method
)

select c.payment_method, c.expected_total, a.actual_total
from {{ ref('payment_control_totals') }} c
left join actual a using (payment_method)
where abs(coalesce(a.actual_total, 0) - c.expected_total) > 0.005
```

Add the processor settlement total (1672.00) in the same way, compared against `sum(orders.amount)`.

**b) A cheap unit-plausibility guard on the raw seed.** Every real payment in this data is at least 100 cents, so tiny non-zero amounts mean the unit is wrong. Add to `seeds:` in `schema.yml` (this is a heuristic, so set the severity to suit your data):

```yaml
  - name: raw_payments
    columns:
      - name: amount
        tests:
          - dbt_utils.expression_is_true:
              arguments:
                expression: "= 0 or amount >= 100"
```

Without `dbt_utils`, use a singular test: `select * from {{ ref('raw_payments') }} where amount > 0 and amount < 100`. On the current data this returns exactly the 12 gift card rows.

**c) Optional: a consistency test between `daily_revenue` and `orders`.** Assert that `sum(revenue)` equals `sum(amount)`. This would have flagged the stale incremental data immediately.
