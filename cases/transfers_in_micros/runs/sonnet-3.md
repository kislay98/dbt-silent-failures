claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 33 s | USD 0.074 | 2026-10-14 | project bef6b69 | prompt 01b9a0af08f9

## 1. Root cause

The bad data is in `raw_payments` (the `seeds/raw_payments.csv` rows, loaded by the ingestion job). From `payment_id` 83 (order 73, 2018-03-16) onward, the `bank_transfer` rows have `amount` in a unit 10,000 times too large. `stg_payments.sql` divides every row by 100 on the assumption of cents, so those rows come out 10,000x too big.

The evidence is in the numbers you posted:
- Order 73 shows 30,000.00, but the customer paid 3.00. That means the raw value is 3,000,000, which is 3.00 in micro-units (6 decimals) rather than cents.
- March bank transfers excluding the four correct orders (57, 61, 64, 67, which total 48) are 1,140,000 against 114 expected, exactly 10,000x. April is 390,000 against 39, also exactly 10,000x.
- Dividing the affected rows by 10,000 gives 3+19+26+25+17+22+2 = 114 for March and 15+14+10 = 39 for April. With the 48 from the correct orders, that is 162 and 39, matching the processor statement.
- Only bank transfers are affected. Non-bank March totals (460) are sane, and payments 76, 77 and 106 look right.
- The `raw_payments.amount` column is a plain `INTEGER`, so nothing in the load rejects the change of scale.

I can't see the seed rows themselves. The payment 83 boundary and the micro-unit explanation are inferred from the outputs. The bank processor's feed most likely changed its unit around 2018-03-12 to 03-16.

Every test passed because none of them checks values. They cover `not_null`, `unique`, `accepted_values` on categories, and a relationship, and all of those still hold when an amount is 10,000x too large.

Downstream impact:
- **`orders`:** bank transfer and total amounts are wrong.
- **`customers`:** lifetime value is wrong.
- **`region_revenue`:** revenue is wrong.
- **`daily_revenue`:** it only rebuilds the last 3 days, so the bad March rows will stay stale after the upstream fix.

## 2. Fix

Fix the data at the source, not with a value-based guess in staging. Correct the affected raw rows (or have the ingestion job normalise the processor's unit), after confirming against the processor statement:

```sql
update raw_payments
set amount = amount / 10000
where payment_method = 'bank_transfer'
  and id >= 83
  and amount >= 10000;   -- guard so it is safe to re-run
```

If it is a seed CSV, make the same edit in `seeds/raw_payments.csv` and run `dbt seed`. Then rebuild with a full refresh so `daily_revenue` drops the stale rows:

```
dbt build --full-refresh
```

If the ingestion job keeps sending micro-units, it needs a fix too. Otherwise the next load reintroduces the problem. As a stopgap, `stg_payments` could branch on the unit for those rows, but that would hardcode a `payment_id` threshold, so I'd only do it temporarily.

## 3. Tests that would have caught it

**a) Plausibility test**, `tests/assert_payment_amounts_plausible.sql`. Tune the threshold to your largest legitimate payment; the largest valid value in the sample is about 26.

```sql
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount < 0
   or amount > 1000
```

**b) Reconciliation against the processor statement.** Add a small seed, `seeds/processor_statement.csv`, with columns `month,payment_method,amount`, and a singular test, `tests/assert_orders_match_processor_statement.sql`. This one would have flagged 1,140,048 against 162 immediately.

```sql
with ours as (
    select date_trunc('month', order_date) as month,
           round(sum(bank_transfer_amount), 2) as amount
    from {{ ref('orders') }}
    group by 1
),
statement as (
    select cast(month as date) as month, amount
    from {{ ref('processor_statement') }}
    where payment_method = 'bank_transfer'
)
select s.month, s.amount as statement_amount, o.amount as orders_amount
from statement s
left join ours o using (month)
where o.amount is distinct from s.amount
```

One further thing to check: `raw_customer_regions` has an `updated_at` column but is documented as one row per customer. If it holds history, `region_revenue` joins fan out and inflate revenue. The only test there is `unique` on `region`. Add `unique` on `customer_id` to that seed to rule it out.
