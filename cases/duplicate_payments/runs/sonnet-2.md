claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 21 s | USD 0.087 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

`seeds/raw_payments.csv` has 12 duplicated payment rows, ids 114-125, appended after the real data (ids 1-113). Each repeats an earlier payment with the same `order_id`, `payment_method` and `amount` under a new `id`:

| dup id | order | method | amount | original id |
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

These add 18,000 cents, which is 180.00 (10+26+8+30+9+8+18+14+5+9+29+14). That is exactly the gap between 1852.00 and the processor's 1672.00. `stg_payments.sql` passes them through, and `orders.sql` sums them per order. That is why order 1 shows 20.00 instead of 10.00 and order 10 shows 52.00 instead of 26.00. The same inflation flows into `customers` (for example, customer 1's lifetime value of 43 should be 33), `region_revenue` and `daily_revenue`.

Every test passed because the only uniqueness test on payments is on `payment_id`, and the duplicates have distinct ids. Nothing checks the natural key or reconciles to an external total.

## 2. Fix

Data fix: delete lines for ids 114-125 from `seeds/raw_payments.csv`, leaving 113 rows. Then rebuild:

```
dbt seed --full-refresh && dbt build --full-refresh
```

The `--full-refresh` matters because `daily_revenue` is incremental and only rebuilds the last 3 days. The inflated history for the older dates would otherwise stay in place.

If the real feed can re-deliver rows like this, also dedupe defensively in `stg_payments.sql`. I checked that none of the 113 original rows share order, method and amount, so this would not drop any legitimate payment today:

```sql
renamed as (

    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is currently stored in cents, so we convert it to dollars
        amount / 100 as amount

    from source
    qualify row_number() over (
        partition by order_id, payment_method, amount
        order by id
    ) = 1

)
```

The catch is that a customer who legitimately makes two identical payments on the same order would be collapsed into one. If that is possible in production, drop the `qualify` and rely on the data fix plus the test below.

## 3. Tests that would have caught it

`tests/assert_stg_payments_no_duplicate_deliveries.sql` (singular test, fails if any rows are returned):

```sql
select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by order_id, payment_method, amount
having count(*) > 1
```

If you use dbt_utils, the equivalent is a model-level `unique_combination_of_columns` test on `stg_payments` over `[order_id, payment_method, amount]`. Run against the current data, either version would have flagged all 12 rows.

If you add the dedupe to `stg_payments`, point the test at `ref('raw_payments')` instead, or it will always pass.

The stronger guard is a reconciliation test against the processor's settlement total. Load that total as a seed, such as `processor_settlements`, and add a test that fails when `abs(sum(orders.amount) - settlement_total) > 0`. That would have shown the 180.00 gap directly.
