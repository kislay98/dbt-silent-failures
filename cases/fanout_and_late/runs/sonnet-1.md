claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 24 s | USD 0.070 | 2026-10-14 | project 8ee5734 | prompt 89287ed414a2

## 1. Root cause

The bug is in `models/daily_revenue.sql`. It's an incremental `delete+insert` model whose only reprocessing rule is:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

That window is keyed on **order date**, but the backfill arrived late by **load time**. Last night's load added six completed February orders (the store whose sync was down). Since the table's max `order_date` is in April, the window only covers early April, so February was never rebuilt. `daily_revenue` keeps its stale February rows, while `orders` (a full-refresh table) picks up the backfill.

The data confirms it. The six mismatching days are exactly the backfilled orders:

| Day | Missing orders | Missing revenue |
|---|---|---|
| Feb 3 | 1 | 12 |
| Feb 8 | 1 | 9 |
| Feb 13 | 1 | 15 |
| Feb 17 | 1 | 6 |
| Feb 22 | 1 | 20 |
| Feb 26 | 1 | 3 |
| **Total** | **6** | **65** |

That is 33 − 27 = 6 orders and 480 − 415 = 65.00. Feb 3 and Feb 22 have no `daily_revenue` row at all.

Every test passed because none of them reconcile `daily_revenue` against `orders`. The tests only check `unique` and `not_null`, and the stale rows satisfy both. The two tables "always matched" only because the sources never arrived late before.

## 2. Fix

Change `daily_revenue.sql` so it also rebuilds any day where the target disagrees with `orders`, not just the trailing three days:

```sql
    {% if is_incremental() %}
    -- payments can land a day or two after the order, so the last three days are rebuilt on every run;
    -- late backfills can land on any date, so also rebuild any day that no longer matches orders
    where order_date >= (select max(order_date) - interval 3 day from {{ this }})
       or order_date in (
            select o.order_date
            from orders o
            left join {{ this }} d using (order_date)
            group by o.order_date, d.orders, d.revenue
            having count(*) <> coalesce(d.orders, 0)
                or abs(coalesce(sum(o.amount), 0) - coalesce(d.revenue, 0)) > 0.005
       )
    {% endif %}
```

Then repair the existing data. Either run `dbt run --full-refresh -s daily_revenue`, or just run the new model normally, since the next incremental run will pick up the six mismatching days. The table is a tiny aggregate, so `materialized='table'` is also a reasonable simplification.

## 3. Test

Add `tests/assert_daily_revenue_matches_orders.sql`, a singular test that fails if any day differs in either direction:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select coalesce(o.order_date, d.order_date) as order_date, o.orders as expected_orders, d.orders as actual_orders,
       o.revenue as expected_revenue, d.revenue as actual_revenue
from o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where coalesce(o.orders, 0) <> coalesce(d.orders, 0)
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

This would have failed last night with the six rows above.

## Separate issue (not verified)

`region_revenue` totals 1838.00 in revenue, against 1737.00 in `orders`. That 101.00 gap is unrelated to this bug. It's probably a join fan-out from `raw_customer_regions`, which has an `updated_at` column and so may hold several rows per customer, while the model joins it without picking the latest. I can't confirm that without the seed data. A `unique` test on `raw_customer_regions.customer_id` would show it.
