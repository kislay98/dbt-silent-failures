claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 26 s | USD 0.006 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

The bug is a unit mismatch in the payments staging model, not in `orders`.

- `stg_payments.sql` divides every payment by 100 with the comment "`amount` is currently stored in cents":
  ```sql
  amount / 100 as amount
  ```
- In `seeds/raw_payments.csv`, the 12 `gift_card` rows are recorded in whole dollars, not cents. Their raw values sum to 205:
  `ids 9, 22, 26, 48, 49, 67, 68, 71, 92, 99, 107, 110` = 23+6+23+18+11+6+28+14+3+30+26+17 = **205**. That matches the provider's 205.00 statement.
- Staging then divides them by 100, so `sum(gift_card)` becomes 2.05 instead of 205.00.

Specific rows:

| payment id | order | method | raw | staged (wrong) | correct |
|---|---|---|---|---|---|
| 9 | 9 | gift_card | 23 | 0.23 | 23.00 |
| 22 | 19 | gift_card | 6 | 0.06 | 6.00 |
| 67 | 58 | gift_card | 6 | 0.06 (order 58 amount 18.06) | 24.00 |
| 68 | 59 | gift_card | 28 | 0.28 | 28.00 |

All 12 gift-card orders (9, 19, 23, 43, 44, 58, 59, 62, 80, 87, 93, 96) are understated. The totals reconcile exactly:

- Current: 1469.05
- Remove the wrong gift-card total: 1469.05 − 2.05 = 1467.00
- Add the correct gift-card total: 1467.00 + 205.00 = **1672.00**, which matches the processor settlement.

The `orders`, `customers`, `daily_revenue`, and `region_revenue` models are all wrong only because they inherit this staged value. For example, `customers` 22 and 66 have understated `customer_lifetime_value`.

**Why the tests pass:** every test is structural (`not_null`, `unique`, `accepted_values` on method and status, `relationships`). None checks that amounts are correct or reconcile to an external source, and none covers `stg_payments.amount`. A value that is 100× too small still satisfies all of them.

## 2. Fix

Make the unit conversion depend on the payment method in `models/staging/stg_payments.sql`:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- card, coupon and bank transfer amounts are stored in cents;
        -- gift card amounts are already whole dollars in the source feed
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount

    from source

)
```

Then rebuild the affected models:

```bash
dbt build --select stg_payments+
```

Expected result afterward: `sum(gift_card_amount)` = 205.00, `sum(amount)` = 1672.00, order 9 amount = 23.00.

This is the fix I'd make now, because the provider statement and the customer's 23.00 redemption both say dollars. The longer-term fix is upstream: the payments feed should use one unit for every method, and the staging model should not need a special case. Confirm with whoever owns the payments extract before you rely on the seed as the source of truth.

## 3. Tests that would have caught it

**a) A dbt unit test that pins the conversion rule.** This fails on the current code, since it returns 0.23 for the gift-card row:

```yaml
# models/staging/unit_tests.yml
unit_tests:
  - name: stg_payments_converts_units_by_method
    model: stg_payments
    given:
      - input: ref('raw_payments')
        rows:
          - {id: 1, order_id: 1, payment_method: credit_card, amount: 1000}
          - {id: 9, order_id: 9, payment_method: gift_card, amount: 23}
    expect:
      rows:
        - {payment_id: 1, order_id: 1, payment_method: credit_card, amount: 10.00}
        - {payment_id: 9, order_id: 9, payment_method: gift_card, amount: 23.00}
```

**b) A reconciliation test against the external statement.** This catches the total drifting from the processor, which is what finance actually noticed. Add the provider's statement as a seed (for example `seeds/raw_gift_card_statement.csv` with one total row), then:

```sql
-- tests/assert_gift_card_total_matches_provider.sql
with ours as (
    select sum(amount) as total from {{ ref('stg_payments') }}
    where payment_method = 'gift_card'
),
provider as (
    select sum(amount) as total from {{ ref('raw_gift_card_statement') }}
)
select ours.total, provider.total
from ours, provider
where abs(ours.total - provider.total) > 0.005
```

A singular test returns rows only on failure, so `dbt build` fails if the totals diverge.

**Side note:** `models/schema.yml` documents `total_order_amount` on `customers`, but that column doesn't exist; the model produces `customer_lifetime_value`. Worth fixing while you're in there.

I haven't run any of this. The analysis is from the files and output you pasted. Running the unit test against the current code should fail on the gift-card row, which would confirm the diagnosis.
