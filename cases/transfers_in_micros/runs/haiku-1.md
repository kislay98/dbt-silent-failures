claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 53 s | USD 0.007 | 2026-10-14 | project bef6b69 | prompt 01b9a0af08f9

## 1. Root cause

**The bad data is in the raw payments table, not in the dbt models.** `raw_payments.amount` is wrong for a set of `bank_transfer` payments. `stg_payments.sql` divides by 100 to convert cents to dollars, which is correct for cents, so the model code is doing its job. The values reaching it are already inflated.

Evidence from the samples:

- **Order 73:** `stg_payments` shows 30000.0, but the customer paid 3.00. The stored value is 10,000× the true amount.
- **March bank transfers:** the affected rows are 30000 + 190000 + 260000 + 250000 + 170000 + 220000 + 20000 = 1,140,000. Dividing each by 10,000 gives 3 + 19 + 26 + 25 + 17 + 22 + 2 = 114. Add the unaffected rows (2 + 16 + 26 + 4 = 48) for a total of 162.00, which matches the processor statement.
- **April bank transfers:** orders 92, 97 and 98 are 150000, 140000 and 100000. Divided by 10,000 these give 15 + 14 + 10 = 39.00, which also matches the statement.
- **Unaffected rows:** Jan/Feb, order 57/61/64/67, and `coupon` payment 106 are all correct. The corruption affects `bank_transfer` only, from roughly payment 83 / 2018-03-16 onward. Payments 76 (order 67, 2018-03-11) and earlier are fine. I can't pin the exact cutoff from these samples, and the raw tables have no load timestamp to show when the rows arrived.

The models are only passing the bad value through:

- `stg_payments.sql` is correct for cents.
- `orders.sql` has no fan-out. Order 92's total of 150002 is 150000 bank + 2 coupon.
- `daily_revenue.sql` and `region_revenue.sql` inherit the same bad amounts.

**Why every test passed:** none of the tests checks the magnitude of `amount`. `not_null` and `accepted_values` only check presence and method names, and nothing reconciles totals to the processor.

Things I ruled out: the `/ 100` cast, a join fan-out in `orders`, and the incremental window in `daily_revenue` (that only explains stale old rows, not the wrong values).

## 2. Fix

**Step 1: correct the raw data at its source.** The ingestion job is the right place to fix this, so the bad load is not repeated. Before changing any rows, confirm with the processor statement that the true amount is `raw / 10,000` for every affected `bank_transfer` row. Then back up the table and patch the rows, or re-ingest the file. I haven't made this change because it modifies the warehouse's raw data, and you should confirm the scope first. Use this to find the affected range:

```sql
select p.id, p.order_id, o.ordered_at, p.payment_method, p.amount
from raw_payments p
join raw_orders o on o.id = p.order_id
where p.payment_method = 'bank_transfer'
  and o.ordered_at >= '2018-03-05'
order by p.id;
```

**Step 2: rebuild everything downstream.** `daily_revenue` is incremental and only rebuilds the last three days, so March and April rows would stay wrong without a full refresh:

```
dbt build --select stg_payments+ --full-refresh
```

Don't patch this in dbt by dividing `amount` for some rows. That would hide the cause and break any future row that is correct.

## 3. A test that would have caught this

Reconcile monthly totals against the processor statement. This catches the problem at month-end, whatever the cause. It needs a `processor_statement_monthly` seed or source with columns `month`, `payment_method` and `amount`, which you would maintain each month.

```sql
-- tests/assert_bank_transfer_matches_processor.sql
with ours as (
    select
        cast(date_trunc('month', o.order_date) as date) as month,
        round(sum(p.amount), 2) as amount
    from {{ ref('stg_payments') }} p
    join {{ ref('stg_orders') }} o on p.order_id = o.order_id
    where p.payment_method = 'bank_transfer'
    group by 1
),

processor as (
    select month, amount
    from {{ ref('processor_statement_monthly') }}
    where payment_method = 'bank_transfer'
)

select
    coalesce(ours.month, processor.month) as month,
    ours.amount as our_amount,
    processor.amount as processor_amount
from ours
full join processor using (month)
where abs(coalesce(ours.amount, 0) - coalesce(processor.amount, 0)) > 0.01
```

As a cheaper backstop that needs no external data, add a row-level range test on `stg_payments.amount` with a `max_value` set from your business rules. A row check would have flagged 30000.00 on an order that should be small. Set it to `warn` severity first if legitimate large payments are possible.

## Other things I noticed

- `stg_*` models use `ref('raw_*')`, which points at seeds. The build log shows no seeds ran, so these tables are being loaded by ingestion. Using `source()` would make that explicit. If a seed with the same name were ever run, it could overwrite the ingested table.
- The build ran 27 of the 29 declared data tests. The two missing ones are the seed tests, because seeds were not part of this build.
- `customers.yml` documents `total_order_amount`, but `customers.sql` has no such column. The model produces `customer_lifetime_value`.
