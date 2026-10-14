claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 39 s | USD 0.007 | 2026-10-14 | project ad0b16b | prompt 5e8b9b5cd1d1

## 1. Root cause

**`jaffle_shop/models/daily_revenue.sql`** is wrong. Its incremental filter never re-reads old dates:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The window is anchored to the newest `order_date` already in the table, not to the data that changed. Once orders exist after February, every run rebuilds only the final few days. February's rows are written once and never revisited, even though payments are loaded later.

`orders` is correct. Its February total of 485.00 matches the settlement, and `orders` is a full table rebuilt on every run. Both models have 27 February orders, so no orders were added or dropped. The 70.00 gap is an amount change on existing orders. Since `orders.amount` is the sum of each order's payments, a late or corrected payment for a February order explains it. `daily_revenue` never picked up that change because the incremental window never reached February.

The schema tests passed because they only check nulls and uniqueness within each model. Nothing compares `daily_revenue` with `orders`.

To confirm, run this against the warehouse. I expect one or more days where `amt` is 70.00 higher than `revenue`:

```sql
select coalesce(o.order_date, d.order_date) as day, o.amt, d.revenue
from (select order_date, sum(amount) as amt from orders
      where order_date >= date '2018-02-01' and order_date < date '2018-03-01'
      group by 1) o
full outer join (select order_date, revenue from daily_revenue
      where order_date >= date '2018-02-01' and order_date < date '2018-03-01') d
  on o.order_date = d.order_date
where abs(coalesce(o.amt, 0) - coalesce(d.revenue, 0)) > 0.005
order by 1;
```

A secondary issue: `orders` builds its amounts from `raw_payments`, which has no load timestamp, so I can't see when the late payment arrived. The fix below doesn't depend on that.

## 2. Fix

`daily_revenue` has 69 rows. Incremental materialization gains nothing at that size, so make it a plain table:

```sql
-- jaffle_shop/models/daily_revenue.sql
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

Then run `dbt run --select daily_revenue`. Because the materialization changes from incremental to table, this rebuilds the whole table and corrects February.

If you want to keep it incremental, you would need to rebuild every date whose orders changed, not a fixed window. That requires a change timestamp on payments, which the raw tables don't have.

In `jaffle_shop/models/schema.yml`, change the `daily_revenue` description. It currently says "rebuilds the last three days on each run," which will no longer be true.

## 3. A test that would have caught this

Add a singular test at `jaffle_shop/tests/daily_revenue_matches_orders.sql`. It fails for any day where `daily_revenue` disagrees with `orders`:

```sql
-- Fails for any order_date where daily_revenue disagrees with orders
with order_totals as (

    select
        order_date,
        sum(amount) as amount,
        count(*) as order_count

    from {{ ref('orders') }}

    group by order_date

)

select
    coalesce(d.order_date, o.order_date) as order_date,
    o.amount as orders_amount,
    d.revenue as daily_revenue,
    o.order_count as orders_count,
    d.orders as daily_revenue_orders

from {{ ref('daily_revenue') }} d

full outer join order_totals o
    on d.order_date = o.order_date

where abs(coalesce(d.revenue, 0) - coalesce(o.amount, 0)) > 0.005
   or coalesce(d.orders, 0) <> coalesce(o.order_count, 0)
```

On the current data this returns the February days that differ by 70.00. It would have failed the first nightly run after the discrepancy appeared.

For the finance close, also compare monthly totals of `orders.amount` against the processor settlement file. That needs the settlement figures loaded into the warehouse, which the ingestion job would have to provide.

## Other things I noticed

- **Seed tests didn't run.** The build log shows 32 data tests found but 28 run. The four tests on `raw_customer_regions` and `raw_payment_methods` are missing, so those seeds are never validated nightly. They didn't cause this bug.
- **Doc drift in `customers`.** `schema.yml` documents `total_order_amount`, but `customers.sql` outputs `customer_lifetime_value`. The column doesn't exist.
