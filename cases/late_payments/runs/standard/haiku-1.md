claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 31 s | USD 0.006 | 2026-10-14 | project ad0b16b | prompt 5e8b9b5cd1d1

## 1. Root cause

The bug is in `jaffle_shop/models/daily_revenue.sql`, in its incremental filter:

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
{% endif %}
```

`daily_revenue` is an incremental model with `delete+insert`, so each run rebuilds only the most recent three days. Any day older than that is frozen at whatever it summed to when it was last built. The comment assumes payments land within a day or two, so the window was meant to cover late payments.

The evidence points to February payments that arrived after their days had left the window:

- `orders` is a full-rebuild table, so its February amount (485.00) is recomputed from all of `stg_payments` each run. It matches the processor settlement.
- `daily_revenue` is the only February figure that is stale. It has the same 27 orders but 70.00 less revenue. Order count is unaffected because the order rows already exist. Only the amounts are short, which is what happens when some of an order's payments were missing when its day was last built.
- January and March agree because their payments had already landed before those days were last rebuilt. March is also inside the window, so it keeps refreshing.

The tests passed because they only check nulls and uniqueness. Nothing compares `daily_revenue` to `orders` or to the settlement, so a frozen value looks valid.

I haven't confirmed the 70.00 is specifically late payments, because `raw_payments` has no landing timestamp. To confirm, compare the two sides by day:

```sql
select o.order_date, o.revenue as orders_rev, d.revenue as daily_rev
from (select order_date, sum(amount) as revenue from orders group by 1) o
full join daily_revenue d using (order_date)
where abs(coalesce(o.revenue,0) - coalesce(d.revenue,0)) > 0.005
order by 1;
```

The mismatched days should all be February dates that are more than three days behind `max(order_date)` in `daily_revenue`.

## 2. Fix

`daily_revenue` has 69 rows, so the incremental optimisation isn't worth the risk. Let it inherit the project default (`+materialized: table`):

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

This removes the `config(...)` block and the `is_incremental()` filter. Then run `dbt run --select daily_revenue`. Because the model is now a table, this rebuilds all days, including February, from `orders`. This is the data fix; the model change is what keeps it correct.

If you need to keep it incremental, the window must cover every date whose payments can still change, which means a lookback on payments (for example, rebuilding from the earliest order date still open for settlement) rather than three days. That is more complex and I wouldn't recommend it at this size.

## 3. Test that would have caught it

Add a singular test that reconciles `daily_revenue` against `orders` for every day. Save it as `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`:

```sql
with orders_by_day as (

    select order_date, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.revenue as orders_revenue,
    d.revenue as daily_revenue

from orders_by_day o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date

where abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

It fails on the current data and on the first nightly run after a payment lands late. For a finance-facing guarantee, add a second check against the processor settlement, which means loading the monthly settlement totals as a seed and asserting that `sum(orders.amount)` matches them per month.

## Other things I noticed (not causing this issue)

- `region_revenue` joins `raw_customer_regions`, which has an `updated_at` column. If a customer ever has more than one row there, `region_revenue` will double-count revenue. Deduplicate to the latest `updated_at` per customer before the join.
- `customers.yml` documents `total_order_amount`, which the model doesn't produce.
