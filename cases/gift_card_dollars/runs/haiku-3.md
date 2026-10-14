claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 34 s | USD 0.007 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

`stg_payments` divides every payment by 100, but the gift card rows in the source are already in dollars.

`models/staging/stg_payments.sql`:
```sql
-- `amount` is currently stored in cents, so we convert it to dollars
amount / 100 as amount
```

The comment is true for `credit_card`, `coupon`, and `bank_transfer` rows, but not for `gift_card`. In `seeds/raw_payments.csv`, the 12 gift card rows hold whole-dollar values (`23` = $23.00), while the other methods hold cents (`1000` = $10.00). The division shrinks every gift card payment 100x.

The affected rows, by raw `payments.id`, are:

| payment id | order | raw `amount` | staged `amount` | should be |
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

The numbers reconcile exactly with finance's figures:
- Raw gift card amounts sum to 205 (dollars), matching the provider statement of 205.00. The staged sum is 2.05, which is 205 / 100.
- Non-gift-card payments total 411 + 185 + 871 = 1467.00. Adding the correct gift card total gives 1467.00 + 205.00 = **1672.00**, the processor settlement figure. The current output is 1467.00 + 2.05 = 1469.05.

The bad values flow into `orders` (`gift_card_amount` and `amount`, for example order 9 at 0.23 and order 87 at 26.30 instead of 56.00), then into `customers` lifetime values, `daily_revenue`, and `region_revenue`.

The tests pass because they only check structure: `not_null`, `unique`, and `accepted_values` on payment method. Nothing checks units or totals against an independent source.

## 2. Fix

Convert cents to dollars only for the methods that are stored in cents. Gift cards are stored in dollars:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is stored in cents for card, coupon and bank transfer payments,
        -- but in dollars for gift cards (confirmed against the provider statement: 205.00)
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount

    from source

)
```

Then rebuild. `daily_revenue` is incremental and only rebuilds the last three days, so its older rows would stay wrong. `--full-refresh` is needed for that model:

```bash
dbt build --select stg_payments+ --full-refresh
```

Before merging, confirm with whoever owns the gift card feed that dollars is the unit going forward. If the feed is supposed to be cents, the fix goes in the export or seed (multiply the 12 gift card rows by 100), not in the staging rule.

Also correct the staging comment so it doesn't mislead the next reader.

Expected results after the fix: `sum(gift_card_amount)` = 205.00, `sum(amount)` = 1672.00, order 9 = 23.00.

## 3. A test that would have caught it

A dbt unit test on `stg_payments` pins the conversion rule per payment method. It fails on the current code and passes after the fix. Add it to `models/staging/schema.yml`:

```yaml
unit_tests:
  - name: stg_payments_converts_units_per_method
    description: Card, coupon and bank transfer amounts are in cents; gift card amounts are in dollars.
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

The unit test checks the rule but not the data, so add a reconciliation test against an external control total. This needs a seed with the provider's statement (`seeds/gift_card_provider_statement.csv`, with columns `order_id,redeemed_amount`, and the 12 orders above). Then add a singular test:

```sql
-- tests/assert_gift_card_total_matches_provider_statement.sql
with staged as (

    select sum(amount) as total
    from {{ ref('stg_payments') }}
    where payment_method = 'gift_card'

),

statement as (

    select sum(redeemed_amount) as total
    from {{ ref('gift_card_provider_statement') }}

)

select staged.total as staged_total, statement.total as statement_total
from staged
cross join statement
where abs(staged.total - statement.total) > 0.005
```

Had this test existed, it would have failed at `dbt build` with 2.05 vs 205.00. The same pattern applies to the processor settlement total.

## Other notes

- I worked from the pasted files and log and did not run dbt, so the fix and tests are unverified in a live run. The arithmetic above matches the output sample exactly.
- `schema.yml` documents `total_order_amount` on `customers`, but the model doesn't produce that column. The docs are wrong, and dbt won't flag it.
