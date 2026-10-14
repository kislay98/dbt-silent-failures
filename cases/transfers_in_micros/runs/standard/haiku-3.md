claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 75 s | USD 0.011 | 2026-10-15 | project ad0b16b | prompt 75e42f540dd8

## 1. Root cause

**The bad values are in the raw `raw_payments.amount` column for `bank_transfer` rows, not in the SQL.** The conversion in `jaffle_shop/models/staging/stg_payments.sql` (`amount / 100`) is correct for the other methods, so the problem is in the ingested data.

- **Other methods are fine in the same table.** `credit_card` and `coupon` rows convert to plausible dollar amounts (19.00, 1.00).
- **Bank transfer rows are off by about 10,000×.** Order 73 has raw amount 3,000,000 cents (30,000.00), but the processor says 3.00 (300 cents). Other examples: raw id 105 (order 92) is 15,000,000 cents, and order 78 is 260,000.00 after conversion.
- **The break is mid-March.** January, February, and early March bank transfers are right, so the `bank_transfer` feed (the `anz` processor in `raw_payment_methods`) or its ingestion changed around then.
- **The excess accounts for the whole March gap.** March bank transfers exceed the statement by 1,140,048 − 162 = 1,139,886. That is exactly the difference between `orders` March amount (1,140,508) and `daily_revenue` March revenue (622).

The samples don't show the mechanism. A factor of 10,000 isn't a simple cents/dollars slip, so it could be two unit conversions or a changed feed unit. The ingestion job will show which.

The bad values then flow into `orders` (amount and fees), `customers` (lifetime values, e.g. customer 90 at 260,017), and `region_revenue` (QLD and WA). Nothing validates amounts, so they pass through.

**`daily_revenue.sql` is a second, separate problem.** Its incremental filter only rebuilds the last three days of the existing table. The March rows were built before the bad payments landed and have never been rebuilt, so they stay stale. The comment says payments land "a day or two" late, but these landed weeks later.

## 2. Fix

**Data (the actual fix):**
1. At the ingestion source, restate the affected `bank_transfer` rows in `raw_payments` from the processor statement, then reload. Don't divide by 10,000 in the model. The rule isn't known, and a model-level divisor would corrupt rows once the feed is fixed.
2. To find the affected rows, run:
   ```sql
   select payment_id, order_id, amount / 100 as dollars
   from raw_payments
   where payment_method = 'bank_transfer' and amount > 100000
   order by amount desc;
   ```
   Check each against the statement.

**Code:**
3. Change `jaffle_shop/models/daily_revenue.sql` to `materialized='table'`. It has 69 rows, so a full rebuild is cheap, and the window can't repair late corrections. If you keep it incremental, the nightly job needs `--full-refresh` at least for this model.
4. Run `dbt build --full-refresh`, then confirm March bank transfers are 162.00 and April is 39.00 in `orders`.

## 3. A test that would have caught it

All 35 nodes passed because the tests only check nulls, uniqueness, and accepted values. None check magnitude or reconciliation. Two tests would close the gap.

**a) Would have caught the stale `daily_revenue` (and the mismatch in the output):**

```sql
-- jaffle_shop/tests/daily_revenue_matches_orders.sql
select coalesce(d.order_date, o.order_date) as order_date
from {{ ref('daily_revenue') }} as d
full outer join (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
) as o
    on d.order_date = o.order_date
where d.orders is distinct from o.orders
   or round(d.revenue, 2) is distinct from round(o.revenue, 2)
```

**b) Would have caught the bad payments, against the processor statement.** Load the statement as a source, then fail when monthly sums by method differ from it by more than 0.01. This is the only check that matches the finance comparison. Without the statement it can't run, so it needs the statement loaded first.

**c) Cheap backstop:** a singular test on `stg_payments` that fails when any single payment exceeds a limit you set from the processor's per-transaction maximum. It catches this class of error but needs a threshold you choose.

## Other issues I noticed

- **Seed tests didn't run.** The log says 32 data tests were found and 28 executed. The four missing are the `raw_customer_regions` and `raw_payment_methods` tests, so the seeds are being excluded from the nightly build. Check the job.
- **Join fan-out risk.** `orders.sql` joins `raw_payment_methods` on `payment_method` and `region_revenue.sql` joins `raw_customer_regions` on `customer_id`. Neither key has a `unique` test. A second bank transfer processor row, or a second `updated_at` version of a customer, would double-count revenue silently. Add `unique` tests on those keys.
- **Docs drift.** `models/schema.yml` documents `total_order_amount` on `customers`, which the model doesn't produce.

I worked only from the pasted output. I haven't queried the warehouse, so the diagnostic query above is the first thing to run to confirm the affected rows.
