claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 35 s | USD 0.195 | 2026-10-14 | project f769fdf | prompt 9b72a4437358

## 1. Root cause

**File:** `jaffle_shop/seeds/raw_payments.csv`, the 12 `gift_card` rows.

Every other payment method is stored in **cents**. The gift card rows are stored in **dollars**. `stg_payments.sql` divides every row by 100 (`amount / 100 as amount`), so each gift card payment ends up 100× too small.

| payment id | order_id | raw `amount` | after `/100` | correct |
|---|---|---|---|---|
| 9 | 9 | 23 | 0.23 | 23.00 |
| 22 | 19 | 6 | 0.06 | 6.00 |
| 26 | 23 | 23 | 0.23 | 23.00 |
| 48 | 43 | 18 | 0.18 | 18.00 |
| 49 | 44 | 11 | 0.11 | 11.00 |
| 67 | 58 | 6 | 0.06 | 6.00 |
| 68 | 59 | 28 | 0.28 | 28.00 |
| 71 | 62 | 14 | 0.14 | 14.00 |
| 92 | 80 | 3 | 0.03 | 3.00 |
| 99 | 87 | 30 | 0.30 | 30.00 |
| 107 | 93 | 26 | 0.26 | 26.00 |
| 110 | 96 | 17 | 0.17 | 17.00 |

The numbers reconcile exactly:
- **Gift card total:** 23+6+23+18+11+6+28+14+3+30+26+17 = 205, which matches the provider statement of 205.00. The model reports 2.05.
- **Order total:** the shortfall is 205.00 − 2.05 = 202.95, and 1469.05 + 202.95 = **1672.00**, which matches the processor settlement.

The models themselves are fine. The bad values flow from `stg_payments` into `orders`, `customers` (lifetime values), `daily_revenue` and `region_revenue`.

The build passed because every test is a `not_null`, `unique`, `accepted_values` or `relationships` check. None of them looks at magnitude, so a value off by 100× passes all of them.

## 2. Fix

The raw contract is "amount in cents", so the fix belongs in the data. Convert the 12 gift card rows to cents in `seeds/raw_payments.csv`:

```diff
-9,9,gift_card,23
+9,9,gift_card,2300
-22,19,gift_card,6
+22,19,gift_card,600
-26,23,gift_card,23
+26,23,gift_card,2300
-48,43,gift_card,18
+48,43,gift_card,1800
-49,44,gift_card,11
+49,44,gift_card,1100
-67,58,gift_card,6
+67,58,gift_card,600
-68,59,gift_card,28
+68,59,gift_card,2800
-71,62,gift_card,14
+71,62,gift_card,1400
-92,80,gift_card,3
+92,80,gift_card,300
-99,87,gift_card,30
+99,87,gift_card,3000
-107,93,gift_card,26
+107,93,gift_card,2600
-110,96,gift_card,17
+110,96,gift_card,1700
```

Then do a full rebuild, because `daily_revenue` is incremental and only rebuilds the last 3 days:

```
dbt build --full-refresh
```

Expected results after the fix:
- `sum(orders.amount)` = 1672.00
- `sum(gift_card_amount)` = 205.00
- order 9 `amount` = 23.00

**If the upstream gift card feed really does send dollars** and will keep doing so, fix it in staging instead so you don't have to patch the data on every load. In `models/staging/stg_payments.sql`:

```sql
        -- `amount` is stored in cents, except gift card payments, which the provider sends in dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount
```

Use one fix or the other, not both. Doing both would make gift card amounts 100× too large.

## 3. Tests that would have caught it

**a) Plausibility check on payment amounts.** Every legitimate payment in this dataset is either 0 or at least $1.00. All 12 bad rows fall between those, and no other row does. Add `tests/assert_no_sub_dollar_payments.sql`:

```sql
-- Amounts are converted from cents in stg_payments; a non-zero payment under $1
-- almost always means the source row was already in dollars (unit mismatch).
select *
from {{ ref('stg_payments') }}
where amount > 0
  and amount < 1
```

This test returns exactly payment ids 9, 22, 26, 48, 49, 67, 68, 71, 92, 99, 107 and 110, so it fails on the current data.

**b) Reconciliation against external control totals.** This is the test that matches what finance actually checks. Add a small seed `seeds/payment_control_totals.csv`, populated from the processor and provider statements:

```csv
payment_method,expected_total
gift_card,205.00
```

Then add `tests/assert_payments_reconcile_to_control_totals.sql`:

```sql
with actual as (

    select payment_method, sum(amount) as actual_total
    from {{ ref('stg_payments') }}
    group by payment_method

)

select
    control.payment_method,
    control.expected_total,
    actual.actual_total
from {{ ref('payment_control_totals') }} as control
left join actual
    on control.payment_method = actual.payment_method
where abs(coalesce(actual.actual_total, 0) - control.expected_total) > 0.01
```

You can add a total-settlement row (1672.00) in the same way to check `sum(orders.amount)`.

Test (a) is cheap and needs no outside data. Test (b) is the real guarantee that the numbers match what finance sees.

**Unrelated issue:** `models/schema.yml` documents a `customers.total_order_amount` column. The model doesn't produce it; the actual column is `customer_lifetime_value`. That doesn't affect these numbers, but the docs should be corrected.
