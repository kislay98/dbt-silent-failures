claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 34 s | USD 0.005 | 2026-10-14 | project bef6b69 | prompt 01b9a0af08f9

## 1. Root cause

The bad values come from the raw payments load, not from the dbt models. `raw_payments.amount` is an INTEGER in cents, and `stg_payments.sql` divides it by 100 (`amount / 100 as amount`), which is correct for cents. But some rows in `raw_payments` are stored at a much larger scale than cents. Those rows pass straight through the model and are never caught.

Evidence:

- **Order 73, payment 83:** the processor shows 3.00, which is 300 cents. The warehouse has 30,000.00 after `/100`, so the raw value is 3,000,000, about 10,000x too large.
- **The affected rows are all late in the data.** Every bank-transfer row from order 73 onward (March 16 to April 7) is in the hundreds of thousands. January, February and early March are fine, so the problem starts at a point in the ingestion history, not across the table.
- **The inflation factor is not constant.** Order 73 is off by 10,000x. The 190,000.00, 260,000.00 and similar values imply different factors, so this is not a single unit error that a `* 100` or `/ 100` would fix. Each affected payment needs to be re-pulled from the processor.

Downstream, `orders.bank_transfer_amount` and `orders.amount` inherit the bad values. `daily_revenue`, `customers` (lifetime value) and `region_revenue` all use the same payments data, so they are wrong too.

The tests pass because they only check uniqueness, not-null and accepted values. Nothing checks that amounts are plausible or that totals reconcile with the processor.

**Files involved:**
- `raw_payments` (loaded by the ingestion job): where the bad data originates
- `models/staging/stg_payments.sql`: passes the bad values through
- `models/orders.sql`, `models/daily_revenue.sql`, `models/customers.sql`, `models/region_revenue.sql`: propagate them

**Open item:** March bank transfers in the unaffected rows sum to 48.00 (orders 57, 61, 64, 67), but the statement says 162.00. Even after removing the inflated rows, about 114.00 is unexplained. That may be from the rows whose true values I can't see, or from a second problem. Reconcile per payment ID before you sign off.

To find every affected row:

```sql
select id, order_id, payment_method, amount
from raw_payments
where amount >= 100000   -- $1,000+ in cents; no single payment should be this large
order by id;
```

## 2. Fix

**Data (required):**
1. Identify every payment whose raw amount is out of range (the query above, plus any rows the statement shows as mismatched).
2. Re-ingest those payments from the processor's statement, or correct them from it. Do not apply a single multiplier. The ratios differ, so a blanket fix will be wrong for some rows.
3. Rebuild everything downstream. `daily_revenue` is incremental and only rebuilds the last 3 days, so it will not repair March and April on its own:

```bash
dbt build --select stg_payments+ --full-refresh
```

**Model (defensive, optional but recommended):** keep `stg_payments.sql` as is, since the cents conversion is correct. If you want the model itself to reject impossible values, add a bound in `models/staging/schema.yml`. Use `dbt_utils.accepted_range` if the package is installed. Choose the bound with finance, because a fixed cap is a guess.

## 3. A test that would have caught this

Reconcile payments to the processor, not to themselves. A payments-only check cannot tell that 30,000.00 is wrong. This singular test requires a `processor_statement` seed (one row per month and payment method, with the statement total):

`tests/assert_bank_transfers_match_processor.sql`:

```sql
with ours as (
    select
        date_trunc('month', p.payment_date) as month,
        round(sum(p.amount), 2) as total
    from {{ ref('stg_payments') }} as p
    where p.payment_method = 'bank_transfer'
    group by 1
),

statement as (
    select
        cast(month as timestamp) as month,
        round(total, 2) as total
    from {{ ref('processor_statement') }}
    where payment_method = 'bank_transfer'
)

select
    coalesce(ours.month, statement.month) as month,
    ours.total as warehouse_total,
    statement.total as statement_total
from ours
full outer join statement on ours.month = statement.month
where abs(coalesce(ours.total, 0) - coalesce(statement.total, 0)) > 0.01
```

This test fails on the current data for March and April, and would have stopped the nightly build from publishing the wrong numbers. You would also want a per-payment version (join on payment ID to the statement) once the statement is loaded at that grain, because a monthly check can hide offsetting errors.

**Note:** `models/schema.yml` lists `total_order_amount` on `customers`, but the model has no such column. The docs are stale and should be corrected separately.
