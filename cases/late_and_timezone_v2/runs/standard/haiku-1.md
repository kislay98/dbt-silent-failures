claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 41 s | USD 0.008 | 2026-10-15 | project ad0b16b | prompt 5e3ee671b766

## 1. Root cause

**The mismatch comes from `models/daily_revenue.sql`, not from the data.** The incremental filter only rebuilds the last three days:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

Last night's load backfilled orders whose `order_date` falls well before that cutoff, which is April. `delete+insert` only rewrites the rows the filter selects, so the backfilled orders never reach `daily_revenue`. `orders` is a table that is fully rebuilt on every run, so it picked them up.

The arithmetic confirms this:

| Month | `orders` (orders / amount) | `daily_revenue` (orders / revenue) | Difference |
|---|---|---|---|
| Jan | 30 / 502.00 | 29 / 496.00 | 1 order / 6.00 = order 100 |
| Feb | 32 / 474.00 | 27 / 415.00 | 5 orders / 59.00 = 65.00 − 6.00 |

Order 100 is the 6.00 coupon payment (payment id 114, 600 cents). Its warehouse timestamp is 31 January, so it belongs to January in both tables, and January's `daily_revenue` row is stale too. The other five backfilled orders are dated in February and are the 59.00 missing from February's `daily_revenue`. Both gaps are the same stale-window problem.

The comment in the model ("payments can land a day or two after the order") describes a different arrival pattern: late payments on recent orders. It does not cover late orders. The window was sized for the wrong kind of lateness.

**The timezone shift is a separate, expected effect, not the cause of the mismatch.** Order 100 is 2018-01-31 23:18:19 in `raw_orders` and 2018-02-01 04:48:19 on the store's screen, a difference of exactly 5h30m. So `ordered_at` is stored in UTC (as `schema.yml` documents: "Date (UTC)") and the store runs at UTC+05:30. Both `orders` and `daily_revenue` use UTC dates, so they agree with each other. Ops reconciles against local dates, so an order placed between 18:30 and 24:00 UTC will always appear one day later on the shop screen. That is a reporting-definition question for ops. I would not change `stg_orders` to local time silently, because the documented contract is UTC and the data does not say which zone each store is in.

I worked from the artifacts you pasted and did not run queries against the warehouse. You can confirm the stale window with `select order_date, orders from daily_revenue where order_date < '2018-03-01' order by 1`, which should show no rows for the backfilled dates.

## 2. Fix

Daily revenue is 69 rows and is cheap to rebuild, so the incremental optimisation is not worth the risk. Drop the incremental logic and let the project default (`+materialized: table`) apply:

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

Then run `dbt run -s daily_revenue` once. This regenerates January and February correctly, and every future backfill is handled automatically.

If you need to keep it incremental for scale, the window must be based on when rows arrived (for example, a loaded-at column), not on `order_date`, and you should run `dbt run -s daily_revenue --full-refresh` after any backfill. The one-time `--full-refresh` is the immediate fix either way.

For ops, the "order 100 is 31 January" difference is expected under the UTC definition. If they need local-date reporting, the change belongs in a reporting layer with a store timezone, not in `stg_orders`.

## 3. Test that would have caught it

Add a singular test that reconciles `daily_revenue` against `orders` on every build. It fails when any day differs, which is exactly what happened here:

```sql
-- tests/assert_daily_revenue_matches_orders.sql
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
full outer join actual
    on expected.order_date = actual.order_date

where expected.orders is distinct from actual.orders
   or round(expected.revenue, 2) is distinct from round(actual.revenue, 2)
```

The existing tests on `daily_revenue` only check uniqueness and not-null, so they pass on a table that is missing rows. This test compares the output to its source, which is the property ops is relying on. Run `dbt test -s assert_daily_revenue_matches_orders` to confirm it fails on the current stale table and passes after the fix.
