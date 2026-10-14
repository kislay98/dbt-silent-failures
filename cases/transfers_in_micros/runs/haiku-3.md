claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 57 s | USD 0.008 | 2026-10-14 | project bef6b69 | prompt 01b9a0af08f9

## Summary

The bad values are in the raw payments data, not in the dbt models. Bank transfer payments from 16 March 2018 onward are stored about 10,000× too large in `raw_payments`. `stg_payments` divides by 100 correctly, and `orders`, `daily_revenue`, and the other models faithfully sum the bad values.

## 1. Root cause

**Evidence.** The processor statement numbers reconcile exactly once the affected rows are scaled down by 10,000:

| Month | Bank transfer in `orders` | Scaled by 1/10,000 | Finance statement |
|---|---|---|---|
| March | 1,140,048.00 | 162.00 | 162.00 |
| April | 390,000.00 | 39.00 | 39.00 |

March is the sum of orders 57, 61, 64, 67 (unchanged, 48.00), plus 73 (3.00), 77 (19.00), 78 (26.00), 84 (25.00), 85 (17.00), 89 (22.00), and 90 (2.00). April is orders 92 (15.00), 97 (14.00), and 98 (10.00).

Order 73 is the first affected order. `stg_payments` divides by 100, so the displayed 30,000.00 means `raw_payments.amount` holds 3,000,000 for payment 83. The correct value for 3.00 is 300 cents. Orders 57 to 67 are correct, which matches "January, February and the first half of March are right."

**Where it is wrong.**
- **Data:** `raw_payments` rows with `payment_method = 'bank_transfer'` from 2018-03-16 onward (at least payment ids 83, 88, 105). The ingestion job loads this table, so I can't see the mechanism that produces the 10,000× factor. I only saw sample rows, so the exact affected set needs a query to confirm.
- **Models:** `stg_payments.sql` is correct for its documented contract (cents, divided by 100). `orders.sql`, `daily_revenue.sql`, and `customers.sql` are correct for their logic. I don't see a model-side defect for this bug.

**Why every test passed.** The tests check presence and membership (`not_null`, `unique`, `accepted_values`). None check magnitude, and nothing reconciles the totals to an external source. A 3,000,000-cent payment passes every test in the project.

**Why it will not heal on its own.** `daily_revenue` only rebuilds the last three days on each run, so rows older than that stay wrong after the source is fixed unless it is fully refreshed.

## 2. Fix

1. **Fix the ingestion job** so bank transfer amounts are converted to cents once. Find out why the break starts on 16 March. Then reload `raw_payments` for the affected window.

2. **Rebuild downstream** after the reload:
   ```
   dbt build --select stg_payments+
   dbt run --select daily_revenue --full-refresh
   ```
   The full refresh is required because the incremental window only covers the last three days.

3. **Interim data fix**, only if the ingestion fix will take time. Confirm the affected ids against the processor statement first, and back up the table:
   ```sql
   create table raw_payments_backup as select * from raw_payments;

   update raw_payments
   set amount = amount / 10000
   where payment_method = 'bank_transfer'
     and id in (/* ids confirmed against the processor statement */);
   ```
   I have not run this. It changes raw data, so it should be confirmed with whoever owns the ingestion job.

## 3. Test that would have caught it

Reconcile monthly totals by payment method against the processor statement. This checks the same number finance checks. It needs a seed with the statement totals, which you would maintain monthly.

`tests/assert_payment_totals_match_processor_statement.sql`:
```sql
with ledger as (

    select
        cast(date_trunc('month', o.order_date) as date) as month,
        p.payment_method,
        sum(p.amount) as total_amount

    from {{ ref('stg_payments') }} as p
    join {{ ref('stg_orders') }} as o
        on p.order_id = o.order_id

    group by 1, 2

),

statement as (

    select month, payment_method, total_amount
    from {{ ref('processor_statement_monthly') }}

)

select
    coalesce(ledger.month, statement.month) as month,
    coalesce(ledger.payment_method, statement.payment_method) as payment_method,
    ledger.total_amount as ledger_total,
    statement.total_amount as statement_total

from ledger
full outer join statement
    on ledger.month = statement.month
   and ledger.payment_method = statement.payment_method

where abs(coalesce(ledger.total_amount, 0) - coalesce(statement.total_amount, 0)) > 0.01
```

Caveats: the statement must be on the same basis as the ledger (order month here, not payment date). Months with a statement row but no ledger rows also fail, which is intended.

A cheaper, complementary guard is a magnitude check on `stg_payments.amount` (for example, `dbt_utils.accepted_range` with a `max_value`). It needs a business threshold for the largest plausible single payment, so I'd treat it as secondary.

## Other things I noticed

- **Test count:** the log says 29 data tests were found but only 27 ran. The two missing are probably the `raw_customer_regions` seed tests, since seeds don't appear in this build. "All tests pass" covers 27 of 29.
- **`region_revenue` may double-count.** `raw_customer_regions` has an `updated_at` column, which suggests history. If a customer has more than one row, `region_revenue` fans out and `sum(payments.amount)` is inflated. The only test on that seed is `not_null`. Consider a `unique` test on `customer_id`, or dedupe to the latest row.
- **Docs drift.** `schema.yml` documents `total_order_amount` on `customers`, but that column does not exist. The model has `customer_lifetime_value`.
