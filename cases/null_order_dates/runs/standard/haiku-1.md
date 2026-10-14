claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 55 s | USD 0.009 | 2026-10-14 | project ad0b16b | prompt 3b339a6e8808

## 1. Root cause

**The `orders` data has 8 rows in `raw_orders` with a NULL `ordered_at`.** Their `order_date` is NULL, and they account for the entire gap: 8 orders and 149.00 in `orders`' NULL-date bucket, which is exactly the difference between the dashboard (35 orders, 622.00) and `orders` (27 orders, 473.00) for March.

Nothing in the pipeline rejects these rows:
- `stg_orders.sql` passes `ordered_at` and `cast(ordered_at as date)` through as NULL.
- `orders.sql` has no date filter, and `models/schema.yml` has no `not_null` test on `orders.order_date`.

**`daily_revenue` is stale for March, and that is why it still shows the old numbers.** It is an incremental model that rebuilds only `order_date >= max(order_date) - 3 days`. The 8 orders were probably dated in March on an earlier run, so their revenue is still sitting under March dates. This is an inference, but the exact match of 8 orders and 149.00 makes it strong. Two things follow:
- The incremental filter `order_date >= ...` never matches NULL, so the table never gets a NULL-date row. The `not_null` and `unique` tests on `daily_revenue.order_date` therefore pass, because the stale rows do have dates.
- `delete+insert` only deletes rows whose keys are in the new batch, so March is never touched.

**`customers` probably explains the "no first order date" report.** `customer_orders` uses `min(order_date)`, which ignores NULLs, and `count(order_id)`, which does not. A customer whose only orders are NULL-dated therefore shows a non-null `number_of_orders` with a NULL `first_order`. The three NULL-`first_order` rows in the sample have NULL `number_of_orders`, meaning they have no orders at all, so they are a different case. I can't identify the two March customers from the sample. Run `select customer_id, order_id from orders where order_date is null` to confirm.

I did not run anything. This is reasoned from the log, the schemas and the sampled outputs.

## 2. Fix

**Data (the real fix):** Find out why the ingestion job loads `ordered_at` as NULL for these 8 orders and restore the timestamps from the source system. Don't backfill dates by guessing. If the source confirms they were placed in March, the 622.00 / 35-order figure is the correct March total and `orders` is the table that is wrong. If they can't be dated, the 149.00 needs its own bucket in the reporting, not a March total.

**`models/daily_revenue.sql`:** make the model exclude NULL dates on every run, so it can't silently keep stale rows or leave them unlabelled:

```sql
    from orders

    where order_date is not null
    {% if is_incremental() %}
      and order_date >= (select max(order_date) - interval 3 day from {{ this }})
    {% endif %}
```

Then rebuild the table once so the stale March rows are dropped:

```
dbt run --select daily_revenue --full-refresh
```

The dashboard and the shop admin both read from this table, so they will show the corrected March figures only after this rebuild. If the admin reads a different source, check it separately.

## 3. Test that would have caught it

Add a `not_null` test on `orders.order_date` in `models/schema.yml`. That catches the NULLs at the source of the problem.

The test that would have caught the stale `daily_revenue` is a reconciliation check, because the current tests only check each table in isolation. Add `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with from_orders as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    where order_date is not null
    group by order_date

),

from_daily as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily_revenue

from from_orders o
full outer join from_daily d
    on o.order_date = d.order_date

where o.orders is distinct from d.orders
   or o.revenue is distinct from d.revenue
```

A singular test passes when it returns no rows. This one would have failed on every March day in the nightly build, because the stale rows sit on March dates while `orders` has 27 orders that day-by-day total less than the dashboard.
