claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 80 s | USD 0.011 | 2026-10-15 | project ad0b16b | prompt 75e42f540dd8

## 1. Root cause

**The bad values are in `raw_payments.amount` (bank-transfer rows, from about mid-to-late March 2018). `stg_payments.sql` converts them without checking them, and nothing in the test suite checks magnitude.**

- **The data:** `raw_payments` is loaded by the ingestion job, not by dbt. Order 73 was paid 3.00, which is 300 cents, but the output shows 30,000.00, so its raw `amount` must be 3,000,000. That is 10,000× too large. The sample also shows bank-transfer rows at 15,000,000 (order 92) and 14,000,000 (order 97). The first half of March is correct and the bad rows start between about 15 and 23 March, so something changed in the bank-transfer feed or its load at that point. The outputs don't show the mechanism. Check the ingestion job and the anz feed for a unit or scale change around that date.
- **Where it passes through:** `models/staging/stg_payments.sql` assumes every row is cents (`amount / 100`). It does not validate that assumption. `orders`, `customers`, `region_revenue` and `daily_revenue` all inherit the inflated amounts.
- **Why the tests pass:** every test checks presence, uniqueness or allowed values. None checks magnitude, and none reconciles to the processor statement. `orders.amount` and `bank_transfer_amount` only have `not_null`. The check that `amount` equals the sum of its method columns also passes, because the bad value is in the amount itself.
- **Confirmation:** the bad amount is exactly what is missing from `daily_revenue`. March `orders` total is 1,140,508.00 and March `daily_revenue` is 622.00. The difference, 1,139,886.00, equals the bank-transfer overstatement (1,140,048 − 162). `daily_revenue` was built before the bad rows existed.

Separately, `daily_revenue.sql` is incremental and only rebuilds the last three days, so it can't correct March. April is mixed, with some days rebuilt and some stale. It is a second defect that the fix has to address, but it is a symptom of the bad data, not its cause.

## 2. Fix

**Data (outside this repo):**
1. Confirm the true bank-transfer amounts against the processor statement, then correct the affected `raw_payments` rows or re-ingest the feed with the right scale. Find the affected rows with:
   ```sql
   select o.order_id, o.ordered_at, p.id, p.payment_method, p.amount as raw_value, p.amount / 100.0 as dollars
   from raw_payments p
   join raw_orders o on o.id = p.order_id
   where p.payment_method = 'bank_transfer'
   order by o.ordered_at;
   ```

**dbt:**
2. Rebuild the incremental model from scratch so March and April are recomputed: `dbt build --full-refresh -s daily_revenue`. The other models are tables and rebuild on each run, so they correct themselves once the raw data is fixed.
3. Do not add a silent divisor or a clamp in `stg_payments.sql`. Without knowing the feed's rule, that hides the problem. Let the test below fail the build instead.

## 3. Test that would have caught this

The statement already exists, so reconcile the ledger to it per month and payment method. This needs the processor statement loaded as a source (`raw_processor_statement`, with `statement_date`, `payment_method`, `amount`). Align the date basis (settlement vs. order date) and add a tolerance for any timing differences.

`jaffle_shop/tests/assert_payments_match_processor_statement.sql`:
```sql
with ledger as (

    select
        date_trunc('month', o.order_date) as month,
        p.payment_method,
        sum(p.amount) as ledger_amount

    from {{ ref('stg_payments') }} p
    join {{ ref('stg_orders') }} o
        on p.order_id = o.order_id

    group by 1, 2

),

statement as (

    select
        date_trunc('month', statement_date) as month,
        payment_method,
        sum(amount) as statement_amount

    from {{ ref('raw_processor_statement') }}

    group by 1, 2

)

select
    ledger.month,
    ledger.payment_method,
    ledger.ledger_amount,
    statement.statement_amount

from ledger
join statement
    using (month, payment_method)
where abs(ledger.ledger_amount - statement.statement_amount) > 0.01
```

On the current data this returns the March and April bank-transfer rows, so it fails the build as intended.

If you don't want the statement dependency yet, add a cheaper guard at the staging layer:

`jaffle_shop/tests/assert_payment_amount_plausible.sql`:
```sql
-- Set the threshold from the largest legitimate payment in history; anything above it needs a human look
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount > 1000
```

Every affected row in the sample is above 1,000, so this would also have failed.

**Other findings to address (not the cause):**
- The four seed tests on `raw_payment_methods` and `raw_customer_regions` never ran. The log says 32 data tests were found but only 28 ran. Also, `raw_payment_methods.payment_method` and `raw_customer_regions.customer_id` have no `unique` test. If `raw_payment_methods` ever gets a second row for a method, `orders` will fan out and double-count.
- `models/schema.yml` documents `customers.total_order_amount`, which doesn't exist. The column is `customer_lifetime_value`.
