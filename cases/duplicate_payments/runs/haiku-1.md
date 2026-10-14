claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 28 s | USD 0.006 | 2026-10-22

## 1. Root cause

The bug is in the raw data, not the SQL. `seeds/raw_payments.csv` contains 12 duplicate payment rows, ids 114–125, appended after id 113. Each one repeats an existing row's `order_id`, `payment_method`, and `amount`, but has a new `id`:

| dup id | order_id | method | amount (cents) | duplicates id |
|---|---|---|---|---|
| 114 | 1 | credit_card | 1000 | 1 |
| 115 | 10 | bank_transfer | 2600 | 11 |
| 116 | 18 | credit_card | 800 | 21 |
| 117 | 26 | credit_card | 3000 | 31 |
| 118 | 36 | bank_transfer | 900 | 41 |
| 119 | 46 | bank_transfer | 800 | 50 |
| 120 | 54 | credit_card | 1800 | 61 |
| 121 | 62 | gift_card | 1400 | 71 |
| 122 | 71 | credit_card | 500 | 81 |
| 123 | 79 | credit_card | 900 | 91 |
| 124 | 88 | credit_card | 2900 | 101 |
| 125 | 97 | bank_transfer | 1400 | 111 |

The duplicates total 18000 cents, or 180.00. That is exactly the gap between finance's 1672.00 and `sum(orders.amount)` = 1852.00.

The SQL is doing what it's told. `stg_payments` passes the rows through, `orders.sql` sums them per order, and `customers.sql` sums them per customer. The duplicates flow through all three.

Affected outputs:
- **`orders`**: order 1 shows 20.00 instead of 10.00. Order 10 shows 52.00 instead of 26.00. Order 18 shows 21.00 instead of 13.00. The same inflation applies to the other nine orders.
- **`customers`**: customer 1 `customer_lifetime_value` is 43.0, should be 33.0. Customer 7 is 52.0, should be 26.0. Customer 64 is 38.0, should be 30.0. Customers 3 and 99 are unaffected.

Why every test passed:
- `unique` on `payment_id` passes because the duplicates have fresh ids (114–125).
- `not_null` and `accepted_values` only check individual values, so the duplicate values are valid.
- No test checks payments for duplicate natural keys, and no test reconciles against an independent control total.

Note: I derived this by hand from the seed files and the sample output. I didn't run dbt here.

## 2. Fix

Remove rows 114–125 from `seeds/raw_payments.csv`. Confirm with the payment processor first that these are retries or double-posts and not real second payments. The fact that they're appended at the end with new ids suggests a re-ingest, but that's for the source system to confirm.

After the change, `dbt build` should give:
- `sum(orders.amount)` = 1672.00 (matches finance)
- order 1 `amount` = 10.00
- customer 1 `customer_lifetime_value` = 33.0, customer 7 = 26.0, customer 64 = 30.0

Optional hardening: if this feed can produce duplicates again, dedupe in `stg_payments` on the processor's own transaction id, if one exists. The seed has no such column, so I'd need the real feed to say what the key should be.

## 3. A test that would have caught this

A singular test in `tests/assert_no_duplicate_payments.sql`:

```sql
-- The same payment should not appear twice for an order.
select
    order_id,
    payment_method,
    amount,
    count(*) as copies
from {{ ref('stg_payments') }}
group by order_id, payment_method, amount
having count(*) > 1
```

This fails on the current data with 12 rows. I checked ids 1–113 by hand and found no legitimate same-method, same-amount pair within an order, so it should pass once the 12 rows are removed.

Don't use a test that reconciles `orders.amount` against `stg_payments`. Both sides come from the same duplicated rows, so it would pass while the numbers are wrong. The reconciliation has to use an independent control total, such as the processor's settlement report. If you can add that as a seed (for example, `seeds/processor_settlement_totals.csv`), a singular test can compare `sum(stg_payments.amount)` against it and would have failed here by 180.00.

## Side issue

`models/schema.yml` documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`. dbt doesn't fail on a documented column that's missing from the model, so this is a docs bug that nothing caught. Rename the entry in `schema.yml` to `customer_lifetime_value`.
