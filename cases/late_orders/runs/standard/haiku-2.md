claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 34 s | USD 0.007 | 2026-10-14 | project ad0b16b | prompt 0a4fd749d3e5

## 1. Root cause

**The bug is in `jaffle_shop/models/daily_revenue.sql`, in the incremental filter.** `orders` is correct. Its February total (480.00 over 33 orders) matches `customers` and `region_revenue`, which both reconcile to 1737. `daily_revenue` is missing 6 February orders worth 65.00.

The gap is exactly the difference between the two models:

| Month | `orders` (orders / amount) | `daily_revenue` (orders / revenue) |
|---|---|---|
| Jan | 29 / 496 | 29 / 496 |
| **Feb** | **33 / 480** | **27 / 415** |
| Mar | 35 / 622 | 35 / 622 |
| Apr | 8 / 139 | 8 / 139 |

Jan, Mar and Apr match exactly. Only February is short, by 6 orders and 65.00.

The mechanism is this:

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
{% endif %}
```

The three-day window is anchored to `max(order_date)` already in the table, which is in early April 2018. On each run, only dates from about April 4 onward are deleted and reinserted. Any February rows that were not present when the table was first built are never revisited. The most likely history is that some February orders arrived in `raw_orders` or `raw_payments` after the table had been built for February, so they were never inserted. The comment in the model says payments can land "a day or two after the order", but the window does not follow that rule. It only covers the table's last few days, so late data for older dates is never picked up.

Nothing failed because the tests cannot see this problem. `unique` and `not_null` on `order_date` still pass when whole days or orders are missing. No test compares `daily_revenue` to `orders`.

I can't query the warehouse from here, so the late-arrival explanation is an inference. Confirm it with:

```sql
select o.order_id, o.order_date, o.amount, o.status
from orders o
left join daily_revenue d using (order_date)
where d.order_date is null
  and o.order_date between date '2018-02-01' and date '2018-02-28';
```

This should return the six missing orders. The `raw_orders` `ordered_at` values will show whether the orders were late-loaded.

## 2. Fix

**Immediate:** rebuild the table from full history:

```bash
dbt run --select daily_revenue --full-refresh
```

**Durable:** stop using the incremental strategy for this model. It has 69 rows and is derived from `orders`, so a plain table is cheap and cannot drift:

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

Remove the `{% if is_incremental() %}` block and the lookback comment with it. I'd rather not tune the window, because no fixed window can catch data that arrives months late.

## 3. Test that would have caught this

Add a singular test at `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`. It fails if any day's order count or revenue differs between `orders` and `daily_revenue`:

```sql
with from_orders as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

),

compared as (

    select
        coalesce(o.order_date, d.order_date) as order_date,
        coalesce(o.orders, 0) as expected_orders,
        coalesce(d.orders, 0) as actual_orders,
        coalesce(o.revenue, 0) as expected_revenue,
        coalesce(d.revenue, 0) as actual_revenue

    from from_orders o
    full outer join {{ ref('daily_revenue') }} d
        on o.order_date = d.order_date

)

select *
from compared
where expected_orders <> actual_orders
   or abs(expected_revenue - actual_revenue) > 0.005
```

It returns a row for every mismatched day, so it fails in the current state, which is the point. Running it on the broken table would have flagged the February gap on the first build. I haven't run it against the warehouse here.
