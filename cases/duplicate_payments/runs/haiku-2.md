claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 42 s | USD 0.008 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

`seeds/raw_payments.csv` contains **12 duplicate payment rows** (ids 114–125). Each is an exact copy of an earlier row in the same order, with the same `payment_method` and `amount`, but a new `id`:

| Duplicate row id | Copy of row id | order_id | payment_method | amount |
|---|---|---|---|---|
| 114 | 1 | 1 | credit_card | 10.00 |
| 115 | 11 | 10 | bank_transfer | 26.00 |
| 116 | 21 | 18 | credit_card | 8.00 |
| 117 | 31 | 26 | credit_card | 30.00 |
| 118 | 41 | 36 | bank_transfer | 9.00 |
| 119 | 51 | 46 | bank_transfer | 8.00 |
| 120 | 61 | 54 | credit_card | 18.00 |
| 121 | 71 | 62 | gift_card | 14.00 |
| 122 | 81 | 71 | credit_card | 5.00 |
| 123 | 91 | 79 | credit_card | 9.00 |
| 124 | 101 | 88 | credit_card | 29.00 |
| 125 | 111 | 97 | bank_transfer | 14.00 |

The duplicates sum to 180.00. That is exactly the gap between the output (1852.00) and finance's settlement total (1672.00).

The mechanism:

- `stg_payments` converts cents to dollars and passes the duplicates through unchanged.
- `orders.sql` pivots payments by `order_id` with `sum(...)`, so each duplicate is added to `orders.amount`. Order 1 has two `credit_card 1000` rows, so it shows 20.00 when the customer was charged 10.00.
- `customers`, `daily_revenue`, and `region_revenue` all sum the same payment rows, so they are inflated too. For example, customer 64 shows 38.00 lifetime value (order 18 is counted at 21.00 instead of 13.00), and customer 1 shows 43.00 instead of 33.00.

The seed is the only place this enters the pipeline. Nothing in the project deduplicates it.

**Why every test passed:** `unique` on `stg_payments.payment_id` checks the wrong key. The duplicate rows have fresh, distinct ids, so the test is satisfied. Nothing checks the business key (order, method, amount), and nothing reconciles the output against an independent control total.

## 2. Fix

**Data fix.** Confirm with the processor that rows 114–125 are duplicate settlement records and not real second charges. The 1672.00 figure matches exactly with these rows removed, which strongly suggests they are duplicates from a re-sent feed. Then remove rows 114–125 from `seeds/raw_payments.csv`, leaving 113 rows, and rebuild:

```bash
dbt build --full-refresh
```

`--full-refresh` matters. `daily_revenue` is incremental and only rebuilds the last three days on each run. Without a full refresh, the historical rows for the affected orders would keep their inflated values.

**Expected results after the fix:**

- `select sum(amount) from orders` returns 1672.00
- Order 1 `amount` is 10.00
- Order 18 `amount` is 13.00
- Customer 1 `customer_lifetime_value` is 33.00, and `completed_lifetime_value` stays at 23.00
- Customer 64 `customer_lifetime_value` is 30.00

If the processor cannot supply a transaction reference, a staging-level dedupe on `(order_id, payment_method, amount)` would also work for this data. I checked the original 113 rows and no legitimate payments share all three values, so it would not drop real payments here. It is still a heuristic, so I would prefer the processor's transaction id as the key if it is available.

## 3. Test that would have caught it

Add a singular test at `tests/assert_no_duplicate_payments.sql`:

```sql
-- Fails if the same payment (order, method, amount) is loaded more than once.
select
    order_id,
    payment_method,
    amount,
    count(*) as copies
from {{ ref('stg_payments') }}
group by order_id, payment_method, amount
having count(*) > 1
```

This test returns 12 rows on the current seed and passes once the duplicates are removed.

For a stronger guard, add a finance control: load the processor's settlement total as a seed and add a singular test asserting that `sum(orders.amount)` equals it. That catches inflation from any source, not just this one.

## Other things I noticed

- `models/schema.yml` documents `customers.total_order_amount`, but `customers.sql` has no such column. The column is `customer_lifetime_value`. Doc drift like this will break a test if one is added against the missing column.
- `region_revenue.revenue` and `daily_revenue.revenue` have only `not_null` tests. They would not have caught the inflation either.
