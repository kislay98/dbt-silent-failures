claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 29 s | USD 0.178 | 2026-10-14 | project f769fdf | prompt d246cb0f008e

# Duplicate payments in the seed inflate `orders.amount` by exactly 180.00

## 1. Root cause

**File:** `jaffle_shop/seeds/raw_payments.csv`, **rows `id` 114–125.**

These 12 rows are copies of earlier payments with new ids. They repeat every 10th original payment (ids 1, 11, 21, …, 111). This looks like a replayed batch from the payments feed.

| dup id | copy of id | order_id | method | amount (cents) |
|---|---|---|---|---|
| 114 | 1 | 1 | credit_card | 1000 |
| 115 | 11 | 10 | bank_transfer | 2600 |
| 116 | 21 | 18 | credit_card | 800 |
| 117 | 31 | 26 | credit_card | 3000 |
| 118 | 41 | 36 | bank_transfer | 900 |
| 119 | 51 | 46 | bank_transfer | 800 |
| 120 | 61 | 54 | credit_card | 1800 |
| 121 | 71 | 62 | gift_card | 1400 |
| 122 | 81 | 71 | credit_card | 500 |
| 123 | 91 | 79 | credit_card | 900 |
| 124 | 101 | 88 | credit_card | 2900 |
| 125 | 111 | 97 | bank_transfer | 1400 |

These rows add up to 18,000 cents, which is **180.00**, and **1852.00 − 180.00 = 1672.00**. That exactly matches the processor settlement total.

The output sample shows the same thing:
- Order 1 is 10.00 + 10.00 = 20.00.
- Order 10 is 26 + 26 = 52.00.
- Customer 1's lifetime value is 43.00. It should be 33.00.

**Why every test passed:** the only uniqueness test on payments is `unique` on `payment_id`. The duplicates have new ids (114–125), so that test passes. Every downstream test only checks for nulls, accepted values, or keys. None of them check amounts.

**Affected models:** `orders`, `customers`, `region_revenue` and `daily_revenue` all read `stg_payments`, so all four are inflated.

## 2. Fix

**Data fix:** delete rows `id` 114–125 from `seeds/raw_payments.csv`. In production, also ask whoever owns the payments feed why the batch was replayed.

**Rebuild:**

```bash
dbt build --full-refresh
```

The `--full-refresh` matters. `daily_revenue` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. The inflated dates are January to April 2018, which is far outside that window. A normal `dbt build` would fix `orders`, but `daily_revenue` would keep the wrong revenue for those dates.

**Don't dedupe in `stg_payments`.** It's tempting to add `select distinct order_id, payment_method, amount`, but two identical split payments on one order can be legitimate. Fix the data at the source, and let a test flag it if it happens again.

## 3. A test that would have caught this

Add a singular test, `tests/assert_no_duplicate_payments.sql`. It fails if the same payment appears more than once under different ids:

```sql
-- the same payment landing twice under different ids (e.g. a replayed feed batch)
select
    order_id,
    payment_method,
    amount,
    count(*) as payment_count

from {{ ref('stg_payments') }}

group by order_id, payment_method, amount

having count(*) > 1
```

On the current data this returns the 12 duplicated payments, and it returns nothing on the original 113 rows. If identical split payments are legitimately possible in your data, set `config(severity='warn')` so a human reviews them. The same check is available as `dbt_utils.unique_combination_of_columns`.

The strongest guard is a reconciliation test. Load the processor's settlement totals (per order or per day) as a source or seed. Then add a singular test that returns any order where `orders.amount` differs from the settled amount. That would catch this bug and any other source of drift, such as missing payments or wrong cent conversions.
