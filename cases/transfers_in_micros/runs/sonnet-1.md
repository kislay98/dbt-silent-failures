claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 28 s | USD 0.069 | 2026-10-14 | project bef6b69 | prompt 01b9a0af08f9

## 1. Root cause

The bad data is in `raw_payments.amount` (the seed/ingestion load), and `models/staging/stg_payments.sql` passes it straight through.

From about 16 March, bank-transfer rows arrive in a different unit. Their `amount` is 10,000x too large after the model's `amount / 100`:

| order | statement | `orders` shows | ratio |
|---|---|---|---|
| 73 | 3.00 | 30,000.00 | 10,000 |
| 77 | 19.00 | 190,000.00 | 10,000 |
| 92 | 15.00 | 150,000.00 | 10,000 |

The totals reconcile exactly:
- **March:** 48 (orders 57 to 67, correct) + 1,140,000 / 10,000 = 162.00.
- **April:** 390,000 / 10,000 = 39.00.

So the raw values for these rows are about 3,000,000 where 300 cents is expected. This looks like the processor or ingestion job switching to 1/10,000-dollar units for bank transfers.

Payments 73 and 76 (Mar 7 and Mar 11) are fine, and 83 (Mar 16) is the first bad one I can see. The exact cut-over `id` is somewhere in 77–83, so I can't pin it down from the samples. I haven't run anything. This is all inferred from the output you pasted.

`stg_payments.sql` hardcodes `amount / 100` with the comment "currently stored in cents". That is true only for the old rows. Nothing in the project checks the magnitude of a payment, so every test passes. The existing tests only check null, unique, accepted payment methods and statuses, and foreign keys.

## 2. Fix

**Data fix.** Correct the raw rows, preferably at the ingestion job as well, so new loads don't repeat it.

First find the boundary:

```sql
select id, order_id, payment_method, amount
from raw_payments
where payment_method = 'bank_transfer' and amount > 100000   -- > $1,000
order by id;
```

Then rescale from the first bad `id` (call it `X`). Check that no good bank-transfer rows exist at or above `X`.

```sql
update raw_payments
set amount = amount / 10000
where payment_method = 'bank_transfer' and id >= X;
```

The divisions are exact (3,000,000 → 300). If the raw table is a seed, edit the CSV the same way.

**Rebuild with `--full-refresh`.** Run `dbt build --full-refresh -s orders+ daily_revenue`, or at least `dbt run --full-refresh -s daily_revenue`. `daily_revenue` is incremental and only rebuilds the last 3 days. Without a full refresh, the inflated March and April rows stay there. `orders`, `customers` and `region_revenue` are tables, so they rebuild normally.

I'd avoid patching this in `stg_payments` with a `case when ... then amount/10000`. It would hide a source-unit change instead of fixing it.

## 3. Test that would have caught it

A singular test on the staged amounts, `tests/assert_payment_amounts_plausible.sql`:

```sql
{{ config(severity='error') }}

-- no single payment should be negative or above the plausible ceiling (AUD)
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount < 0
   or amount > {{ var('max_payment_amount', 1000) }}
```

This fails immediately on payments 83, 88, 105 and the rest. Pick the ceiling from your real maximum order size.

For stronger protection, add a monthly reconciliation of `orders` against the processor statement as a seed or source. A month-over-month anomaly check on `sum(bank_transfer_amount)` would also have flagged a roughly 7,000x jump.

**Side risk, unrelated to this bug.** `raw_customer_regions` has an `updated_at` column but the schema only tests `not_null`. If a customer has more than one row, `region_revenue` double-counts revenue. Add `unique` on `customer_id` there.
