claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 28 s | USD 0.178 | 2026-10-14 | project f769fdf | prompt d246cb0f008e

## 1. Root cause

**`seeds/raw_payments.csv`, rows `id` 114–125, are duplicates of earlier payments.** Each has a new `payment_id` but the same `order_id`, `payment_method` and `amount` as an existing payment:

| dup id | order_id | method | amount (¢) | original id |
|---|---|---|---|---|
| 114 | 1 | credit_card | 1000 | 1 |
| 115 | 10 | bank_transfer | 2600 | 11 |
| 116 | 18 | credit_card | 800 | 21 |
| 117 | 26 | credit_card | 3000 | 31 |
| 118 | 36 | bank_transfer | 900 | 41 |
| 119 | 46 | bank_transfer | 800 | 51 |
| 120 | 54 | credit_card | 1800 | 61 |
| 121 | 62 | gift_card | 1400 | 71 |
| 122 | 71 | credit_card | 500 | 81 |
| 123 | 79 | credit_card | 900 | 91 |
| 124 | 88 | credit_card | 2900 | 101 |
| 125 | 97 | bank_transfer | 1400 | 111 |

These rows add up to 18,000¢ = **180.00**, and 1852.00 − 180.00 = **1672.00**, which matches the settlement total exactly. In the output, order 1 shows 20.00 (should be 10.00) and order 10 shows 52.00 (should be 26.00). The same inflation flows into `customers` (customer 1's lifetime value is 43.00, should be 33.00), `daily_revenue`, and `region_revenue`.

**Why every test passed:** `unique_stg_payments_payment_id` only checks the surrogate `id`, and the duplicates have fresh ids. No test checks whether the same payment appears twice, and none reconciles against an external total. The model SQL is correct: `orders.sql` aggregates payments per order before the join, so it doesn't fan out rows.

## 2. Fix

**Delete rows 114–125 from `seeds/raw_payments.csv`**, then rebuild:

```bash
dbt build --full-refresh
```

The `--full-refresh` is required. `daily_revenue` is incremental and only rebuilds `order_date >= max(order_date) - 3 days` (from 2018-04-06 on). Every affected order is dated 2018-01-01 through 2018-04-07, so a normal run would leave most of the inflated daily revenue in place.

I'd fix this in the data rather than with a `distinct`/`row_number()` dedupe in `stg_payments`. The feed has no timestamp or processor reference to tell a re-sent row from a real second charge, and the data contains legitimate same-method split payments (order 18: credit card 500 + 800; order 79: 1800 + 900). A generic dedupe could silently drop real payments later. Ask whoever produces the payments feed why rows were re-emitted with new ids. If it will keep happening, have them add a processor transaction id and dedupe on that in staging.

## 3. Test that would have caught it

Add a singular test, `tests/assert_no_duplicate_payments.sql`:

```sql
-- A payment re-sent by the feed with a new id still has the same order, method and amount
select
    order_id,
    payment_method,
    amount,
    count(*) as payment_count

from {{ ref('stg_payments') }}

group by order_id, payment_method, amount

having count(*) > 1
```

On the current data this returns 12 rows and fails the build. After the fix it returns nothing. (If you use dbt_utils, `unique_combination_of_columns` on those three columns does the same thing.) If a genuine identical repeat charge is ever possible, set `severity: warn` so it's flagged for review without blocking the build.

For the stronger long-term guard, load the processor settlement totals as a source and add a reconciliation test asserting `sum(orders.amount)` equals the settlement total per period. That would catch any discrepancy, not just this kind of duplicate.
