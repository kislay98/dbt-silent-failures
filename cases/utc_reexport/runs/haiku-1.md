claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 42 s | USD 0.006 | 2026-10-14 | project bef6b69 | prompt f253c60e27c9

## 1. Root cause

**Primary: `stg_orders.sql` derives `order_date` as the UTC date, but the business reports by Indian local day.**

```sql
cast(ordered_at as date) as order_date,
```

`ordered_at` is a naive `TIMESTAMP` in UTC. India is UTC+05:30, so any order placed after 18:30 UTC falls on the next local day. Order 73 shows this directly:

- `ordered_at = 2018-03-15 19:45:20` (UTC) → `order_date = 2018-03-15` in the warehouse
- 19:45:20 UTC + 5:30 = 2018-03-16 01:15:20 IST, which is the admin screen's 16 March

The raw data is correct. The date is being taken in the wrong timezone. `orders.sql` passes `order_date` through, so `orders` inherits the bug. The shift is roughly the same across days, so monthly totals still agree. The daily disagreements are the orders placed after 18:30 UTC on each day.

**Secondary: `daily_revenue.sql` has stale history.** The current code aggregates `orders.order_date`, so it is UTC. But the dashboard matches the admin screen for January through March, which means the historical rows were written on the local-day basis. The incremental filter only rebuilds `max(order_date) - 3 days` onward, so those older rows are never recomputed. The five dashboard-only rows (where the `order_date` column is NULL in the comparison) are the same effect: local dates that have no UTC equivalent.

I can't see the run history, so I'm inferring that the rows came from an earlier definition of the model or the date column. Checking the git history of `daily_revenue.sql`, or comparing old rows against `orders` day by day, would confirm this.

**Why the tests passed:** every test checks uniqueness, not-null, or accepted values. None of them checks that the values are correct. The `order_date` description says "UTC", so no test ever asserted the reporting timezone.

## 2. Fix

I chose these; redirect me if you want a different convention.

**a. `models/staging/stg_orders.sql`:** add a local date and keep the UTC one under its own name.

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date_utc,
        cast((ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata' as date) as order_date_local,
        status

    from source

)
```

The `AT TIME ZONE 'UTC'` step makes the naive timestamp explicitly UTC before converting, so the result doesn't depend on the session timezone.

**b. `models/orders.sql`:** make `order_date` the local day, which is what ops reports against, and keep the UTC date as `order_date_utc`.

```sql
select
    orders.order_id,
    orders.customer_id,
    orders.order_date_local as order_date,
    orders.order_date_utc,
    orders.status,
    ...
```

**c. `models/daily_revenue.sql`:** make it a plain table. The incremental window is what allowed the frozen rows, and a table rebuild is cheap at this size.

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

Then run `dbt build --select stg_orders+` (or the full build). Because the table is rebuilt from scratch, no `--full-refresh` is needed.

**d. `models/schema.yml`:** update the `orders.order_date` description to "Date (Asia/Kolkata) the order was placed", and add `order_date_utc` with its own description. Leave `customers.first_order` and `most_recent_order` as UTC, since their descriptions already say UTC. If ops wants those in local time too, that's a separate decision.

## 3. A test that would have caught this

Two singular tests in `tests/`. The first pins the timezone rule with a known boundary case. The second reconciles `daily_revenue` against `orders`, which would have failed on the first run.

`tests/assert_order_date_is_local_day.sql`:

```sql
-- order 73 was placed 2018-03-15 19:45:20 UTC, which is 2018-03-16 01:15 IST
select order_id, order_date
from {{ ref('orders') }}
where order_id = 73
  and order_date <> date '2018-03-16'
```

`tests/assert_daily_revenue_matches_orders.sql`:

```sql
select coalesce(d.order_date, o.order_date) as order_date
from {{ ref('daily_revenue') }} d
full outer join (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
) o using (order_date)
where d.orders is distinct from o.orders
   or d.revenue is distinct from o.revenue
```

The reconciliation test should be run against the full history, not just the last few days, since the stale rows were outside the incremental window.

## Verification

I haven't run these changes; this analysis is based on the files and outputs you provided. After the fix, these should hold:

- `select order_date, count(*) from orders where order_date between '2018-01-05' and '2018-01-18' group by 1` matches the admin screen (5th 2, 7th 1, 9th 1, 11th 1, 12th 1, 14th 2, 15th 1, 17th 3, 18th 2).
- Order 73 is on 16 March.
- The reconciliation test returns no rows.

## Other things I noticed (not the cause)

- `region_revenue.sql` joins `raw_customer_regions`, which has an `updated_at` column. If the feed keeps one row per customer per update, the join fans out and inflates `customers`, `orders`, and `revenue`. The description says "one row per customer", so either the feed or the description is wrong. Worth checking before trusting that model.
- `schema.yml` documents `customers.total_order_amount`, but no model produces that column.
