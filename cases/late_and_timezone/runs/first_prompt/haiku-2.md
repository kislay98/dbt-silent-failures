claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 42 s | USD 0.007 | 2026-10-14 | project 8ee5734 | prompt 0a1f2799e0c1

## 1. Root cause

**The dashboard is wrong because `daily_revenue` is incremental and never revisits old dates.** The problem is in `jaffle_shop/models/daily_revenue.sql`.

`orders` is a full `table` rebuild, so it picks up the backfilled orders. `daily_revenue` only rebuilds `order_date >= max(order_date) - 3 days`. The table already holds data through April, so the window starts in early April. The six backfilled orders are dated January and February, outside that window, so their rows are never inserted into `daily_revenue`. The `delete+insert` never touches those dates, and the dbt tests can't see the gap because they check each model in isolation.

The numbers confirm this. Comparing the two tables by month:

| Month | `orders` | `daily_revenue` | Difference |
|---|---|---|---|
| Jan | 30 / 502.00 | 29 / 496.00 | 1 order / 6.00 |
| Feb | 32 / 474.00 | 27 / 415.00 | 5 orders / 59.00 |
| **Total** | | | **6 orders / 65.00** |

The discrepancy query lists exactly six dates: 01-31, 02-02, 02-08, 02-13, 02-22, 02-25. These match the UTC `order_date` of order_ids 100–105 in `stg_orders`, which I infer are the backfill.

**Timezone is a separate, secondary issue.** `stg_orders.sql` sets `order_date = cast(ordered_at as date)`, which is the UTC date (the schema docs say "Date (UTC)"). The shop is in UTC+05:30. Order 100 is stored as `2018-01-31 23:18:19`, which is `2018-02-01 04:48:19` locally, the time the sync screen shows. All six backfilled orders map to the local February dates ops listed (1, 3, 8, 13, 22, 26). So ops and the warehouse disagree on the date for the first order, and that will also happen for any order placed between 00:00 and 05:30 local time. This timezone shift does not cause the dashboard mismatch, which would occur even with UTC dates, but it does explain why ops sees the first order in February while `orders` shows it in January.

## 2. Fix

**Stop the window from missing old rows.** `daily_revenue` is a `group by` over a few hundred rows, so incremental materialization gives no real benefit here. Change it to a table:

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

Put `{{ config(materialized='table') }}` at the top (in `dbt_project.yml` if you want it project-wide), and update the description in `models/schema.yml`, which still says "Incremental, rebuilds the last three days."

Until that change ships, you can correct the table immediately with:

```
dbt build --select daily_revenue --full-refresh
```

**Timezone.** The raw data doesn't need changing. The team needs to decide which date ops should use. If the local date is what matters for reconciliation, add a column in `stg_orders.sql`, for example `order_date_local` as the UTC timestamp converted to `Asia/Kolkata`, and have ops reconcile on that. Keep `order_date` as UTC, since it is documented that way. I haven't run this conversion, so check it against one of the six orders before relying on it.

**Check the seed before trusting amounts.** `orders.sql` joins payments to `raw_payment_methods`, and the seed only has a `not_null` test on `payment_method`. If a payment method appears in more than one processor row, payments fan out and `amount` is inflated for every order. The current output can't reveal this, because `orders` and `daily_revenue` both sum the same inflated amount. Add a `unique` test on `raw_payment_methods.payment_method` to rule it out.

## 3. Test that would have caught this

Add a singular test that reconciles `daily_revenue` against `orders` for every date. Per-column tests can't catch cross-model drift, which is why all 35 nodes passed.

```sql
-- jaffle_shop/tests/assert_daily_revenue_matches_orders.sql
with orders_by_day as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily_revenue

from orders_by_day o

full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date

where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Run against the current warehouse, this test fails on the six dates above. It runs after both models in `dbt build`, so a stale incremental table fails the build instead of reaching the dashboard.

I haven't run any of this. The analysis is based on the log, the schema, and the sample outputs you pasted.
