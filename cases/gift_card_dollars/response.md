## 1. Root cause

`models/staging/stg_payments.sql` divides every payment by 100:

```sql
amount / 100 as amount   -- "amount is currently stored in cents"
```

That is only true for `credit_card`, `coupon` and `bank_transfer`. The 12 `gift_card` rows in `seeds/raw_payments.csv` are already in dollars, so they get shrunk 100x:

| raw_payments id | order_id | raw amount | in staging | should be |
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

The raw gift card total is 205, and `stg_payments` shows 2.05. The figures reconcile to finance's:

- Gift cards: 205.00 vs 2.05, a 100x error.
- Settlement: 1469.05 − 2.05 + 205.00 = **1672.00**.

The error flows into `orders.gift_card_amount` and `orders.amount`. It also flows into `customers.customer_lifetime_value`; for example customer 53 shows 16.23 and should be 39.00.

Everything passed because no test checks a value or a reconciliation. `not_null`, `unique`, `accepted_values` and `relationships` all hold for wrong numbers.

I reasoned this from the files and output you pasted and haven't run the fix.

## 2. Fix

In `models/staging/stg_payments.sql`:

```sql
        -- credit_card, coupon and bank_transfer are stored in cents;
        -- gift_card is already in dollars
        case
            when payment_method = 'gift_card' then amount
            else amount / 100
        end as amount
```

Confirm with whoever owns the source that gift cards are dollars. The 205.00 provider statement supports that. If the source feed is meant to be uniform, fix the data instead and multiply the gift card rows in the seed by 100.

After `dbt build`, expect order 9 = 23.00, order 87 = 56.00, `sum(gift_card_amount)` = 205.00, and `sum(amount)` = 1672.00.

## 3. Test that would have caught it

Add a singular test, `tests/assert_orders_reconcile_to_finance.sql`, using the control totals finance supplied:

```sql
with expected as (
    select 'gift_card' as metric, 205.00 as expected_total
    union all
    select 'all_methods', 1672.00
),

actual as (
    select 'gift_card' as metric, sum(gift_card_amount) as actual_total from {{ ref('orders') }}
    union all
    select 'all_methods', sum(amount) from {{ ref('orders') }}
)

select expected.metric, expected.expected_total, actual.actual_total
from expected
join actual using (metric)
where abs(expected.expected_total - actual.actual_total) > 0.005
```

Before the fix this returns both rows and fails the build. The hardcoded totals are tied to this seed snapshot, so move them into a seed or a source-of-truth table if the data will change.

A dbt unit test on `stg_payments` with one row per payment method would also catch it earlier. It would assert that `gift_card` 23 gives 23.00 and `credit_card` 2300 gives 23.00.

One unrelated item: `customers` in `schema.yml` documents `total_order_amount`, but the model outputs `customer_lifetime_value`.
