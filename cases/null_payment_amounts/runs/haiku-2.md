claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 49 s | USD 0.007 | 2026-10-14 | project bef6b69 | prompt f5793c9fa6c4

**Short answer:** the settlement (1533.00) is probably right and `orders` is wrong. `daily_revenue` looks right only because its old rows were built before the payment amounts went missing, and the incremental model never rebuilds them. I haven't run any queries, so this is based on the pasted output.

## 1. Root cause

**The data:** some rows in `raw_payments.amount` are NULL. `stg_payments.sql` passes them through (`amount / 100` is NULL when `amount` is NULL), and `orders.sql` uses `sum(...)`, which silently skips NULLs. The sample shows this for:
- Payment 30 (order 25, coupon) is NULL. Order 25 settled at 58.00 = 42.00 + 16.00, so the missing coupon payment should be 16.00.
- Payments 15 and 62 (bank_transfer), and 67 (gift_card), are also NULL in the sample.

The Jan–Mar gap is 65.00. Order 25 accounts for 16.00, so about 49.00 comes from other NULL payments outside the sample. To find them:

```sql
select id, order_id, payment_method
from raw_payments
where amount is null
order by order_id;
```

I can't tell from the pasted output why the loader writes NULL. Check whether a cast or a field mapping in the ingestion job changed for these payment methods.

**Why `daily_revenue` still says 1533.00:** it is `incremental` with a 3-day rebuild window. The Jan–Mar rows were written while those payments had amounts, and the window never revisits them. Its Jan–Mar total is 496 + 415 + 622 = 1533, which matches the settlement. April matches because it falls inside the rebuilt window. So the dashboard isn't validating `orders`. It is stale history.

**Why the tests passed:**
- Nothing tests `stg_payments.amount` for NULLs.
- `not_null` on `orders.amount` only checks the sum, which is non-null whenever at least one payment row exists.
- Nothing reconciles `daily_revenue` against `orders`.

## 2. Fix

**Data:**
- Fix the ingestion job so it stops writing NULL amounts. Confirm the units first: `stg_payments` divides by 100, so raw values are in cents.
- Backfill the NULL payments from the processor's settlement records. Payment 30 should be 1600 cents, which is 16.00 after the divide.

**Code:**

`models/staging/schema.yml`: add a test so NULL amounts fail the build instead of disappearing:

```yaml
  - name: stg_payments
    columns:
      - name: payment_id
        tests:
          - unique
          - not_null
      - name: amount
        tests:
          - not_null
```

This test will fail until the backfill is done. That is intended.

`models/daily_revenue.sql`: a 3-day window can't correct history, so I'd drop the incremental logic and make it a table. The table is small.

```sql
{{ config(materialized='table') }}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

If you want to keep it incremental, run `dbt build --full-refresh -s daily_revenue` once after the data fix. The old rows will still be stale the next time the data changes, so the table is the safer option.

Then run `dbt build` to rebuild `orders` and `daily_revenue`.

## 3. A test that would have caught this

Two tests together would have caught this on the first bad load:

1. The `not_null` on `stg_payments.amount` above. It fails on payments 15, 30, 62, and 67.
2. A singular test that reconciles `daily_revenue` against `orders` for every date. Save it as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
select
    coalesce(o.order_date, d.order_date) as order_date,
    o.amount as orders_amount,
    d.revenue as daily_revenue
from (
    select order_date, sum(amount) as amount
    from {{ ref('orders') }}
    group by order_date
) o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where abs(coalesce(o.amount, 0) - coalesce(d.revenue, 0)) > 0.005
```

Against the current data, this returns the January–March dates where the two differ.

**Other things I noticed:**
- The log shows 27 of 29 data tests ran. The two missing ones are likely the `raw_customer_regions` seed tests, which weren't part of this build. Check that they run in CI.
- `region_revenue` uses an inner join to `raw_customer_regions`. Any order whose customer has no region row drops out silently, and that would produce the same kind of gap.
