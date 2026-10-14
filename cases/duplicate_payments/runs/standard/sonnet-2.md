claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 34 s | USD 0.133 | 2026-10-14 | project ad0b16b | prompt b041d9ee24db

## 1. Root cause

`seeds/raw_payments.csv` contains 12 duplicated payments, ids 114-125, appended after the real data (ids 1-113). Each repeats the order, method and amount of an earlier row under a new `id`:

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

These sum to 18,000 cents, i.e. 180.00, and 1852.00 − 180.00 = 1672.00, which is the processor total. Order 1 has payments 1 and 114, each 10.00, so it shows 20.00 instead of 10.00.

The duplicates flow through `stg_payments` into `orders`, `customers`, `region_revenue` and `fees`. `customers.lifetime_value` and `region_revenue` both total 1852.00.

**Why every test passed:**
- `unique_stg_payments_payment_id` only checks the surrogate `id`, and the duplicates have fresh ids.
- No test covers the natural key (`order_id, payment_method, amount`) or reconciles to an external total.

**Second problem: `daily_revenue` is out of sync with `orders`.**
- It totals 1686 against 1852 for `orders`.
- The incremental model only rebuilds the last 3 days.
- The duplicates arrived after the older days were built, so Jan-Mar are never restated.
- The Jan-Mar gaps (74, 35, 57) are exactly the duplicates for those months.
- April includes the 14.00 duplicate for order 97, because it falls inside the 3-day window.
- Its Jan-Mar figures are therefore correct only by accident. Any late-arriving payment older than 3 days would be missed the same way.

## 2. Fix

Remove rows 114-125 from `seeds/raw_payments.csv`, so it has 113 data rows. Then rebuild, using a full refresh so the stale `daily_revenue` is replaced:

```bash
dbt build --full-refresh
```

Expected afterwards:
- `sum(orders.amount)` = 1672.00
- order 1 amount = 10.00
- `daily_revenue` and `region_revenue` both total 1672.00
- fees drop, since the duplicates were being charged fees too

I would fix the source rather than deduplicate in `stg_payments`. A legitimate repeat charge of the same amount and method on one order would be silently dropped. No such case exists in this data, but the feed owner should find out why the rows were duplicated.

## 3. Tests

Duplicate natural key in `tests/assert_no_duplicate_payments.sql`:

```sql
select
    order_id,
    payment_method,
    amount,
    count(*) as n,
    min(payment_id) as first_payment_id,
    max(payment_id) as last_payment_id

from {{ ref('stg_payments') }}

group by order_id, payment_method, amount
having count(*) > 1
```

On the current data this returns 12 rows and fails. After the fix it returns none.

`daily_revenue` against `orders` in `tests/assert_daily_revenue_matches_orders.sql`, which would have caught the stale incremental:

```sql
select *
from (
    select
        (select sum(revenue) from {{ ref('daily_revenue') }}) as daily_total,
        (select sum(amount) from {{ ref('orders') }}) as orders_total
)
where abs(daily_total - orders_total) > 0.005
```

Longer term, load the processor's settlement report as a seed or source and add a test comparing it to `sum(orders.amount)`. Finance's 1672.00 check would then be automated.

I haven't run any of this, since the files weren't available in the environment. The row analysis comes from the CSVs and outputs you pasted.
