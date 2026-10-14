claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 39 s | USD 0.007 | 2026-10-14 | project ad0b16b | prompt 0a4fd749d3e5

## 1. Root cause

The bug is in `jaffle_shop/models/daily_revenue.sql`. It is an incremental model that only rebuilds the last three days of `order_date` on each run:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The filter is based on the newest date already in `daily_revenue`, which is in early April. February falls outside the window, so its rows are never rewritten. Any February orders that were added to `orders` after February was last written stay missing from `daily_revenue` indefinitely.

The numbers point to this:

- `orders` has 33 February orders, and `daily_revenue` has 27. The 6-order gap matches the gap in revenue: 480.00 − 415.00 = 65.00.
- January, March, and April match exactly.
- Overall: 1737.00 − 1672.00 = 65.00, the same gap.
- A count mismatch means orders were missing from the aggregate entirely. Late payments would change `amount` but not the count, and `orders.amount` has no nulls (its `not_null` test passes), so this is not a null-amount problem.

Nothing failed because no test compares `daily_revenue` to `orders`. The schema tests on `daily_revenue` (`unique`, `not_null`) cannot detect a stale row.

The comment in the model says the window handles payments that land a day or two late. That is not what it protects against. Late-arriving orders dated older than three days fall outside the window and are never picked up.

I can't see the six February orders in the sample, so the diagnosis rests on the counts. To confirm, run this against the warehouse. Every mismatched date should fall in February:

```sql
select o.order_date,
       count(*)        as orders_now,
       d.orders        as orders_in_daily,
       sum(o.amount)   as revenue_now,
       d.revenue       as revenue_in_daily
from orders o
left join daily_revenue d using (order_date)
group by o.order_date, d.orders, d.revenue
having count(*) <> coalesce(d.orders, 0)
order by o.order_date;
```

## 2. Fix

Materialize `daily_revenue` as a table. It has 69 rows built from a table that is already materialized, so the incremental logic costs more than it saves and is what allows the staleness.

`jaffle_shop/models/daily_revenue.sql`:

```sql
{{
    config(
        materialized='table'
    )
}}

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

After this change, a normal `dbt run --select daily_revenue` (or `dbt build`) rebuilds the whole table and restores February. I have not run anything, so this is untested against your warehouse.

If you need to keep it incremental for volume reasons, the window has to be based on what changed, not on the target table's max date. A fixed window cannot cover arbitrarily late orders.

## 3. Test that would have caught it

Add a singular test at `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`. The project already sets `test-paths: ["tests"]`. The test compares each date in `daily_revenue` to the source of truth, `orders`, and returns any date where they disagree:

```sql
with orders_by_date as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

),

daily as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders  as expected_orders,
    d.orders  as actual_orders,
    o.revenue as expected_revenue,
    d.revenue as actual_revenue

from orders_by_date o
full outer join daily d
    on o.order_date = d.order_date

where d.orders is distinct from o.orders
   or abs(coalesce(d.revenue, 0) - coalesce(o.revenue, 0)) > 0.005
```

Against the current data this test fails, returning the February dates. That is the point: it would have failed on the first run after the late orders arrived, and `dbt build` would have stopped before finance saw the numbers. A per-date comparison is stronger than a total-only check, because the monthly offsets could cancel out in a total.
