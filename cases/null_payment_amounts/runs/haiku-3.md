claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 34 s | USD 0.005 | 2026-10-14 | project bef6b69 | prompt f5793c9fa6c4

## 1. Root cause

The problem is in the raw payments data, not in the dbt models. Four payments have a `NULL` amount, and the models silently drop them.

- **Where the NULLs come from:** `stg_payments.sql` passes `amount / 100` straight through, so a `NULL` in `raw_payments.amount` stays `NULL`. The sample shows NULL amounts for payments 15, 30, 62, and 67. `raw_payments.amount` is a nullable `INTEGER`, and no test checks it. I inferred the raw NULLs from the staging output, so confirm with `select id, amount from raw_payments where id in (15, 30, 62, 67)`. A likely cause is a bad cast in the ingestion job, such as a non-integer string like `"16.00"` being cast to `INTEGER` and becoming NULL, but I can't confirm that from here.
- **Why order 25 is short by 16.00:** its payments are 20.00 (bank_transfer), 22.00 (credit_card), and NULL (coupon, payment 30). Settlement is 58.00, so the coupon payment should be 16.00. `orders.sql` uses `sum(...)`, which ignores NULLs, so the order shows 42.00.
- **Why the Jan–Mar gap is 65.00:** the four NULL payments are all in Jan–Mar (orders 13, 25, 54, and 58). Order 25 accounts for 16.00 of the 65.00.
- **Why the tests pass:** the per-method columns in `orders.sql` use `else 0`, so they are never NULL, and the `not_null` tests on them pass. `orders.amount` is non-null because the other payments on each order are non-NULL. Nothing tests `raw_payments.amount` or compares payment totals to order totals.
- **Why `daily_revenue` agrees with settlement:** its Jan–Mar rows total 1533.00, which matches settlement. It is an incremental model that only rebuilds the last three days, so those rows were written when the payments were intact and have never been rebuilt. Its agreement is a stale-data coincidence. It is currently disagreeing with `orders` (1533 vs 1468), and that disagreement is the signal the tests miss.

## 2. Fix

**Data (required):** get the correct amounts for payments 15, 30, 62, and 67 from the processor or the source system, correct `raw_payments` through the ingestion job, and then fix the ingestion cast so it fails loudly instead of producing NULLs.

**Code:**

- Don't coalesce NULL to 0 in staging, because that would hide the problem again. Let the new test (below) fail the build until the data is corrected.
- Rebuild `daily_revenue` from scratch once the data is fixed. The 3-day incremental window will never revisit Jan–Mar:
  ```
  dbt build --full-refresh -s daily_revenue+
  ```
- Consider making `daily_revenue` a plain table, since it's small, and dropping the incremental logic. The 3-day window can't correct anything older than three days, so it can't absorb late or corrected payments.

## 3. Tests that would have caught this

Add these as singular tests under `tests/`:

`tests/assert_payments_have_amounts.sql`:
```sql
select payment_id
from {{ ref('stg_payments') }}
where amount is null
```

`tests/assert_daily_revenue_matches_orders.sql`:
```sql
with daily as (
    select sum(revenue) as total from {{ ref('daily_revenue') }}
),

orders_total as (
    select sum(amount) as total from {{ ref('orders') }}
)

select *
from daily
cross join orders_total
where abs(daily.total - orders_total.total) > 0.005
```

The first test fails on the four NULL payments right away. The second would have caught the 1533 vs 1468 mismatch on the first run after the data changed, which is the discrepancy finance spotted. Once the data is corrected and `daily_revenue` is rebuilt, both tests should pass.
