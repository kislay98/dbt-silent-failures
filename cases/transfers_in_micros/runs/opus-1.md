claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 34 s | USD 0.143 | 2026-10-14 | project f769fdf | prompt a489473ed9d8

# Bank transfer revenue is inflated 10,000× from 2018-03-16

## 1. Root cause

The bad data is in **`raw_payments`**, which the ingestion job loads; the project reads it through `ref('raw_payments')`. Starting with **payment 83 (order 73, 2018-03-16)**, `bank_transfer` rows are stored in **millionths of a dollar instead of cents**. That makes them 10,000× too large.

`models/staging/stg_payments.sql` assumes every row is in cents and divides by 100:

| order | raw `amount` | stg (`/100`) | actual |
|---|---|---|---|
| 64 (pmt 73) | 2,600 | 26.00 | 26.00 ✅ |
| 67 (pmt 76) | 400 | 4.00 | 4.00 ✅ |
| 73 (pmt 83) | 3,000,000 | 30,000.00 | 3.00 ❌ |
| 77 (pmt 88) | 19,000,000 | 190,000.00 | 19.00 ❌ |

Dividing the bad rows by 10,000 reproduces the processor statement exactly:
- **March:** 2 + 16 + 26 + 4 = 48 correct, plus 1,140,000 / 10,000 = 114, gives **162.00** ✅
- **April:** 390,000 / 10,000 = **39.00** ✅

Other payment methods in the same period are fine (coupon 2.00 on order 92, credit card 19.00 on order 67). Only bank transfers changed units.

No test failed because nothing checks magnitude. The column is still `INTEGER`, non-null, and the method is valid. The error flows unchanged into `orders`, `customers`, `region_revenue` and `daily_revenue`.

## 2. Fix

**Preferred: fix the data at the source.** Have the ingestion job convert bank-transfer amounts back to cents, and backfill the affected rows:

```sql
update raw_payments
set amount = amount / 10000
where payment_method = 'bank_transfer'
  and id >= 83;   -- confirm the cutover first, see the query below
```

Before running that, confirm the cutover point. I only saw some of the payment IDs, so check every bank transfer from 2018-03-10 onward. All rows from the cutover on should be exact multiples of 10,000:

```sql
select p.id, p.order_id, o.ordered_at, p.amount, p.amount % 10000 = 0 as looks_micros
from raw_payments p join raw_orders o on o.id = p.order_id
where p.payment_method = 'bank_transfer' and o.ordered_at >= '2018-03-10'
order by p.id;
```

**Stopgap in code** if the source can't be fixed right away, in `stg_payments.sql`:

```sql
        -- `amount` is stored in cents, except bank transfers from payment 83 onward,
        -- which ingestion loads in millionths of a dollar
        case
            when payment_method = 'bank_transfer' and id >= 83 then amount / 1000000
            else amount / 100
        end as amount
```

**Then rebuild `daily_revenue` with a full refresh.** It is incremental and only rebuilds the last 3 days on each run. Without a full refresh, the rows for 2018-03-16 through early April keep the inflated values:

```bash
dbt build --full-refresh -s stg_payments+
```

## 3. A test that would have caught this

Add a range check to `stg_payments.amount` in `models/staging/schema.yml`. Put it on the staging model, not the raw seed. Your nightly build doesn't run seed tests: the log found 29 tests but ran 27, and the 2 skipped are the `raw_customer_regions` tests.

`tests/generic/accepted_range.sql`:
```sql
{% test accepted_range(model, column_name, min_value, max_value) %}
select *
from {{ model }}
where {{ column_name }} < {{ min_value }}
   or {{ column_name }} > {{ max_value }}
{% endtest %}
```

`models/staging/schema.yml`:
```yaml
  - name: stg_payments
    columns:
      ...
      - name: amount
        tests:
          - not_null
          - accepted_range:
              arguments:
                min_value: 0
                max_value: 1000   # AUD per payment; set to a sane ceiling for the business
```

This would have failed on payment 83 the first night. If you already use `dbt_utils`, its `accepted_range` test does the same job.

For a second safety net, add a monthly reconciliation test that compares bank-transfer totals against the processor statement, if finance can expose that as a table.

Separately, `models/schema.yml` documents a `customers.total_order_amount` column that doesn't exist; the model outputs `customer_lifetime_value`.
