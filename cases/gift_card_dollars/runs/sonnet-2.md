claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 30 s | USD 0.103 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

`models/staging/stg_payments.sql` divides every payment by 100 on the assumption that `amount` is in cents:

```sql
amount / 100 as amount
```

That holds for `credit_card`, `coupon` and `bank_transfer`, but the 12 `gift_card` rows in `seeds/raw_payments.csv` are already in whole dollars. They get divided by 100 a second time:

| payment id | order | raw | staged | should be |
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

The raw values sum to 205, which matches the provider statement. Staged, they sum to 2.05, so `orders.gift_card_amount` is 2.05.

The other number reconciles the same way: 871 + 411 + 185 + 205 = 1672.00, finance's figure. The model currently gives 1469.05, which is 202.95 short (205 − 2.05).

Every test passed because they only check `not_null`, `unique`, `accepted_values` and `relationships`. None of them checks a value. The mistake is also hard to spot in the data, since gift card rows are small anyway and none are null or out of range.

The wrong amounts also flow into:
- `customers.customer_lifetime_value` (customers 11, 22, 46, 53, 66, etc.).
- `region_revenue.revenue`.
- `daily_revenue.revenue`.

## 2. Fix

Convert cents only for the methods that are in cents, in `stg_payments.sql`:

```sql
        -- `amount` is stored in cents for every method except gift_card,
        -- which the gift card feed already delivers in dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount
```

I'd fix it in staging rather than editing the seed, so the raw feed stays untouched. If your source team confirms the gift card feed should have been in cents, fix it at the source instead and revert this change.

`daily_revenue` is incremental and only rebuilds the last 3 days, so every earlier day keeps the old wrong revenue. After the fix, rebuild it fully:

```
dbt build --full-refresh -s stg_payments+
```

After that, `sum(orders.gift_card_amount)` should be 205.00, `sum(orders.amount)` should be 1672.00, and order 9 should show 23.00.

## 3. Test that would have caught it

Add a seed, `seeds/provider_statement_totals.csv`, holding the control totals from the provider and processor statements:

```csv
payment_method,total
bank_transfer,411.00
coupon,185.00
credit_card,871.00
gift_card,205.00
```

I took the non-gift-card figures from the current output. They sum with 205 to finance's 1672.00, but please check them against the actual statements.

Then add a singular test, `tests/assert_payment_totals_match_provider_statements.sql`:

```sql
with expected as (

    select payment_method, cast(total as decimal(18,2)) as expected_total
    from {{ ref('provider_statement_totals') }}

),

actual as (

    select payment_method, cast(sum(amount) as decimal(18,2)) as actual_total
    from {{ ref('stg_payments') }}
    group by 1

)

select
    coalesce(expected.payment_method, actual.payment_method) as payment_method,
    expected.expected_total,
    actual.actual_total
from expected
full outer join actual
    on expected.payment_method = actual.payment_method
where expected.expected_total is distinct from actual.actual_total
```

On the old code this fails with one row, `gift_card`, expected 205.00 and actual 2.05. It also fails if a payment method appears or disappears.

Control totals have to be updated each period. If that is too much upkeep, a cheaper guard is a unit-sanity check on `stg_payments`. For example, fail if any method's average payment is more than about 10x smaller than the others. It's cruder, but it would have flagged the gift card method.
