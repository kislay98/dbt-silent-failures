claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 44 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt 150d20052204

## 1. Root cause

**`models/daily_revenue.sql`, lines 3-5 and 24-27.** `daily_revenue` is an incremental model with `delete+insert` on `order_date`, and its incremental filter only reprocesses recent days:

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
{% endif %}
```

The cutoff is based on the latest date already in the target table, which is early April 2018. The backfilled orders are dated 1-26 February, so they fall below the cutoff and are never selected. Those February rows in `daily_revenue` are never rebuilt. `orders` is a full table rebuild, so it includes the backfill, and the two disagree only for February.

The lookback comment (payments landing a day or two late) describes a different problem. Before this load, every order arrived inside the 3-day window, which is why the two tables always matched. Nothing in the project protects against a late-arriving order that is older than the window.

Nothing in the raw data or in `orders.sql` is wrong. The tests pass because they check properties of `daily_revenue` on its own (unique and not-null). None of them compare it against `orders`.

Two things I ruled out or could not confirm:

- **Timezone:** `2018-02-01 04:48:19` is 1 February in any zone from UTC-4 to UTC+19, so the first backfilled order is not a timezone artifact. `orders.order_date` is a UTC date and ops reconciles by local date, so a different order placed near midnight UTC could still land on a different day. Check the other five timestamps.
- **The numbers don't tie exactly:** the output shows 27 orders / 415.00 against 32 / 474.00, a gap of 5 orders and 59.00. The six backfilled orders total 65.00. Either one backfilled order is already in `daily_revenue`, or the figures in the report are slightly off. I have not run any queries, so the per-day diff below should settle it.

To find the exact days that differ:

```sql
select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_dashboard,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_dashboard
from (select order_date, count(*) as orders, sum(amount) as revenue from orders group by 1) o
full join daily_revenue d on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
order by 1;
```

## 2. Fix

`daily_revenue` has about 69 rows and no downstream models. Incremental materialization buys nothing here and breaks whenever late data arrives. Make it a plain table by removing the config block and the `is_incremental()` filter. The project default is already `table`.

`models/daily_revenue.sql`:

```sql
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

Then rebuild it:

```
dbt run --select daily_revenue
```

No `--full-refresh` is needed, since this is no longer incremental. Rerun the diff query above; it should return no rows.

No change is needed to the raw data or to `orders`. The backfill is correct.

## 3. Test that would have caught it

Add a singular test that requires `daily_revenue` to agree with `orders` for every date. It would fail on the current data, which is the point.

`tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must agree with orders on every order_date
with expected as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}
    group by order_date

),

actual as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue

from expected
full join actual
    on expected.order_date = actual.order_date

where expected.orders is distinct from actual.orders
    or abs(coalesce(expected.revenue, 0) - coalesce(actual.revenue, 0)) > 0.005
```

This test would have failed the night the backfill arrived, and it covers any future late-arriving data, not just this one case.
