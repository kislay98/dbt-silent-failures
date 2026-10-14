claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 26 s | USD 0.067 | 2026-10-14 | project bef6b69 | prompt 01b9a0af08f9

## 1. Root cause

The bad data is in `raw_payments` (seed `seeds/raw_payments.csv`, loaded by the ingestion job). From about payment 83 (order 73, 2018-03-16) onward, **`bank_transfer` rows are in a different unit**. They are 10,000x too large once `stg_payments.sql` applies `amount / 100`. I can't see the seed data, so this is inferred from the samples.

- Order 73's customer paid 3.00, which is 300 cents. The staged amount is 30,000.00, so the raw value is 3,000,000.
- 3,000,000 is 3.00 × 1,000,000. The feed switched from cents to millionths of a dollar for bank transfers.
- The ratios fit: March is 1,140,048 against 162 on the statement. Subtracting the correct first half (2+16+26+4 = 48) leaves 1,140,000 against 114, which is exactly 10,000x. April is 390,000 against 39, also 10,000x.
- Only `bank_transfer` is affected. Payment 87 (credit card), payment 106 (coupon) and every payment before payment 77 are fine.

`stg_payments.sql` hard-codes "amount is in cents" for every method and every date. Nothing downstream complains, because the tests only check `not_null`, `unique` and `accepted_values`, and none of those look at magnitude. `orders`, `customers`, `region_revenue` and `daily_revenue` all inherit the inflated amounts.

## 2. Fix

First find the exact boundary. I can't tell which payment_id between 78 and 83 first uses the new unit:

```sql
select id, order_id, amount from raw_payments
where payment_method = 'bank_transfer' and id between 76 and 90 order by id
```

The proper fix is for the ingestion job to load the correct unit, or to add a unit column. Until then, normalise in `stg_payments.sql`. Set `first_micro_payment_id` to the boundary found above, or 83 if that query confirms it:

```sql
{% set first_micro_payment_id = 83 %}
...
    select
        id as payment_id,
        order_id,
        payment_method,

        -- `amount` is in cents, except bank_transfer rows from payment {{ first_micro_payment_id }}
        -- onward, which arrive in millionths of a dollar
        case
            when payment_method = 'bank_transfer' and id >= {{ first_micro_payment_id }}
                then amount / 1000000.0
            else amount / 100
        end as amount
```

Then rebuild everything, with a full refresh for the incremental model. `daily_revenue` only recomputes the last three days, so older bad rows would otherwise stay:

```
dbt build --full-refresh
```

Afterwards, March bank transfers should be 162.00 and April 39.00, and order 73 should show 3.00.

## 3. Tests that would have caught it

**a. Magnitude check on staging.** This would have failed on the first bad load. Add `dbt_utils` to `packages.yml`, then in `models/staging/schema.yml`:

```yaml
      - name: payment_id
        ...
    columns:
      - name: amount
        tests:
          - dbt_utils.accepted_range:
              arguments:
                min_value: 0
                max_value: 5000   # largest plausible single payment, AUD
```

**b. Reconciliation against the processor statement.** This catches any unit or volume drift, not just extreme values. Create `tests/assert_bank_transfer_matches_statement.sql` after loading the statement as a seed or source:

```sql
select o.m, o.bank_transfer, s.statement_amount
from (
    select date_trunc('month', order_date) as m, sum(bank_transfer_amount) as bank_transfer
    from {{ ref('orders') }} group by 1
) o
join {{ ref('processor_statement') }} s using (m)
where abs(o.bank_transfer - s.statement_amount) > 0.01
```

The statement seed name and its columns are placeholders, so adjust them to match yours.

**Side notes**
- `models/schema.yml` documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`. This is a separate doc mismatch.
- `region_revenue` refs a seed, `raw_customer_regions`, that has an `updated_at` column. It may hold several rows per customer, which would fan out the join. That is worth a `unique` test on `customer_id`.
