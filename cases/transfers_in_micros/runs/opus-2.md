claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 40 s | USD 0.151 | 2026-10-14 | project f769fdf | prompt a489473ed9d8

## 1. Root cause

The bug is in the data, not the SQL. From about 2018-03-16, the ingestion job started loading **bank-transfer** rows into `raw_payments.amount` in **micro-units (1/1,000,000 of a dollar)** instead of **cents**. `models/staging/stg_payments.sql` assumes every row is in cents (`amount / 100 as amount`), so each affected payment comes out **10,000× too large**.

The numbers match this exactly:

- **Order 73:** the customer paid $3.00. In micros that is 3,000,000. Dividing by 100 gives 30,000.00, which is what `orders` shows.
- **March:** the correct rows are 2 + 16 + 26 + 4 = 48. The wrong rows sum to 1,140,000, and 1,140,000 / 10,000 = 114. 48 + 114 = **162.00**, which matches the processor statement.
- **April:** 390,000 / 10,000 = **39.00**, which also matches.
- **Scope:** other methods on the same orders are still in cents (order 92 coupon = 2.00, order 67 credit card = 19.00). So only `bank_transfer` rows are affected. The switch falls between payment id 76 (order 67, 03-11, correct) and id 83 (order 73, 03-16, wrong).

Why every test passed: the tests in `models/schema.yml` and `models/staging/schema.yml` only check `unique`, `not_null`, `accepted_values` and `relationships`. Nothing checks the size of `amount`. The column stays `INTEGER` and the method is still the valid value `bank_transfer`, so the unit change is invisible to those tests.

Two things worth checking:
- **Order 77, credit-card payment of 0.00 (id 87).** It may be legitimate, but check it in the raw data while you are there.
- **Seeds weren't built in this run.** The log says "Found … 4 seeds" but runs none. The warehouse `raw_payments` table therefore comes from the ingestion job, not from `seeds/raw_payments.csv`. Fix the loaded table and the job, not the CSV.

Confirm where the switch happens before changing anything:

```sql
select id, order_id, payment_method, amount
from raw_payments
where payment_method = 'bank_transfer'
order by id;
```

## 2. Fix

**a) Fix the source.** The ingestion job should emit cents for bank transfers again. Otherwise the next load brings the problem back.

**b) Correct the loaded rows** (use the boundary id confirmed by the query above):

```sql
update raw_payments
set amount = amount // 10000
where payment_method = 'bank_transfer'
  and id >= 83;
```

If you can't edit the raw table until the ingestion fix ships, patch it in `stg_payments.sql` as a stopgap. Use an explicit cutover rather than a size heuristic, because a $100 payment in cents (10,000) is also a multiple of 10,000:

```sql
        -- `amount` is stored in cents, except bank transfers loaded from payment 83 onwards,
        -- which the ingestion job wrote in micros (1/1,000,000 dollar)
        case
            when payment_method = 'bank_transfer' and id >= 83 then amount / 1000000
            else amount / 100
        end as amount
```

**c) Rebuild with a full refresh.** `daily_revenue` is incremental and only rebuilds the last 3 days. Without a full refresh, its March and April rows keep the inflated revenue:

```
dbt build --full-refresh --select stg_payments+
```

`orders`, `customers` and `region_revenue` are tables, so they will be correct after that rebuild.

## 3. A test that would have caught it

Add a range check on payment amounts. You can add it as a singular test, `tests/assert_payment_amount_in_plausible_range.sql`:

```sql
-- a single payment above $1,000 almost certainly means a unit error upstream
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount < 0
   or amount > 1000
```

Or, if you use `dbt_utils`, add it to `models/staging/schema.yml`:

```yaml
      - name: amount
        tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 1000
```

Set the threshold from your real distribution. Normal monthly totals here are a few hundred dollars, so 1,000 per payment is generous and would still have failed on every bad row from 2018-03-16 on.

If finance can supply the processor statement as a table, a stronger check is a reconciliation test: monthly `sum(bank_transfer_amount)` in `orders` should equal the processor total within a tolerance.
