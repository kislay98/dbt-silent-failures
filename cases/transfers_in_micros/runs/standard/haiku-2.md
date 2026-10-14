claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 71 s | USD 0.011 | 2026-10-15 | project ad0b16b | prompt 75e42f540dd8

## 1. Root cause

The bad values are in the **raw `raw_payments` table**, not in the dbt SQL. Some `bank_transfer` rows have amounts that are **10,000× too large**. The ingestion job loads these rows, and nothing in the repo catches them.

The arithmetic fits the ×10,000 factor exactly:

- **Order 73:** the customer paid 3.00, which is 300 cents. The output shows 30,000.00, so the raw value is 3,000,000 cents.
- **April:** the processor shows 39.00 and the output shows 390,000.00, which is 39 × 10,000.
- **March:** the processor shows 162.00 and the output shows 1,140,048.00. Split 162 = 114 + 48. The 114.00 is inflated by 10,000 to 1,140,000, and the 48.00 is correct, giving 1,140,048.

`stg_payments.sql` divides by 100 correctly for cents. The model logic is sound for the stated convention. The defect is that some rows violate the convention, and `stg_payments` passes them through with no check. The downstream models then propagate the bad values:

- `jaffle_shop/models/staging/stg_payments.sql`: the entry point. It has no range or unit validation.
- `jaffle_shop/models/orders.sql`: `bank_transfer_amount`, `amount`, and `fees` inherit the error.
- `jaffle_shop/models/customers.sql`: `customer_lifetime_value` and `completed_lifetime_value` inherit it.
- `jaffle_shop/models/region_revenue.sql`: `revenue` sums payments directly, so it inherits it.
- `jaffle_shop/models/daily_revenue.sql`: the incremental model rebuilds only the last three days, so it mixes states. March rows are correct (622 = 460 + 162) because they were built before the bad rows arrived. April rows in the rebuild window carry the inflated values, which is why they don't reconcile.

Every test passes because `not_null`, `unique`, and `accepted_values` check presence and membership, not magnitude or agreement with the processor.

Two things I could not confirm from what was provided:

- I inferred the affected rows from the totals and the sample. I have not seen the full `raw_payments` table.
- I can't tell from the log why the ingestion job produces the bad rows. It starts somewhere in the second half of March, so check what changed in the bank transfer feed or ingestion around that date.

## 2. Fix

**a) Stop the bad data at the source.** Fix the ingestion job for the bank transfer feed. Otherwise the next nightly load will overwrite any manual correction.

**b) Correct the existing rows.** Back up first, then confirm the rows against the processor statement before updating:

```sql
create table raw_payments_bak as select * from raw_payments;

-- review the candidates first
select p.id, p.order_id, o.ordered_at, p.amount, p.amount / 100.0 as dollars
from raw_payments p
join raw_orders o on o.id = p.order_id
where p.payment_method = 'bank_transfer'
  and p.amount >= 1000000
order by o.ordered_at;

-- once the list matches the processor statement
update raw_payments
set amount = amount / 10000
where payment_method = 'bank_transfer'
  and amount >= 1000000
  and amount % 10000 = 0;
```

Order 73 should then read 300 cents, which is 3.00 in `stg_payments`.

**c) Rebuild.** `daily_revenue` is incremental and only rebuilds the last three days, so its older rows stay wrong until you force a full refresh:

```
dbt build --full-refresh -s daily_revenue
dbt build
```

**d) Check the seeds.** The header reports 32 data tests, but only 28 ran. The four missing tests are the seed tests on `raw_customer_regions` and `raw_payment_methods`. The log also shows no seed runs. Confirm that the nightly job runs `dbt seed` or includes the seeds in `dbt build`, so the fee rates in `orders.sql` are current.

## 3. A test that would have caught this

The check that would have caught this is a reconciliation against the processor, which is what finance did by hand. It needs a monthly processor statement loaded as a seed, `raw_processor_statement` (`month`, `payment_method`, `amount`). Then add a singular test:

```sql
-- tests/assert_payments_match_processor.sql
with ours as (
    select
        cast(date_trunc('month', o.order_date) as date) as month,
        p.payment_method,
        sum(p.amount) as amount
    from {{ ref('stg_payments') }} p
    join {{ ref('stg_orders') }} o on p.order_id = o.order_id
    group by 1, 2
)

select
    s.month,
    s.payment_method,
    s.amount as processor_amount,
    coalesce(o.amount, 0) as warehouse_amount
from {{ ref('raw_processor_statement') }} s
left join ours o
    on s.month = o.month and s.payment_method = o.payment_method
where abs(coalesce(o.amount, 0) - s.amount) > 0.01
```

This test would have failed on the first nightly run after the bad rows arrived, and it would have pointed at the March and April bank transfer totals.

A cheaper complement is a magnitude guard in `tests/assert_payment_amounts_plausible.sql`:

```sql
select payment_id, order_id, payment_method, amount
from {{ ref('stg_payments') }}
where amount <= 0
   or amount > {{ var('max_payment_amount', 1000) }}
```

The threshold of 1,000 is a placeholder. Set it from the business's real order sizes. This would also have flagged the bad rows, but it only catches values above the threshold. The processor reconciliation catches any error that moves a total.

## Other things I noticed

- `jaffle_shop/models/schema.yml` documents `total_order_amount` on `customers`, but the model doesn't produce that column. The model produces `customer_lifetime_value`.
- `region_revenue` uses inner joins, so customers without a region row are silently dropped. The totals reconcile today, but this is worth a test if the regions feed is incomplete.
