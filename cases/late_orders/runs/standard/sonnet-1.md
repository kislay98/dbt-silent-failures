claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 26 s | USD 0.092 | 2026-10-14 | project ad0b16b | prompt 0a4fd749d3e5

## 1. Root cause

**`models/daily_revenue.sql`**: the incremental filter only reprocesses a short window, and that window is anchored to the data rather than the run date:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The data runs through early April 2018, so on every run only about 6-9 April is rebuilt. Every earlier `order_date` row in `daily_revenue` is frozen at whatever was true when that day was first built.

The evidence:
- `orders` is rebuilt from scratch each run (table, built from the reloaded seeds), so it is always current.
- The whole gap is in February: 33 vs 27 orders (6 missing) and 480 vs 415 revenue (65 missing). The overall gap of 1737 vs 1672 is the same 65. January, March and April match exactly.
- So 6 February orders (or their payments) reached the raw data after those February days were first materialized. This is a late backfill or restatement weeks after the fact, far outside the "a day or two" assumption in the model's comment. `delete+insert` on `order_date` never touches those days again.

No test caught it because the `daily_revenue` tests only check `unique` and `not_null`. Nothing compares it to `orders`, and every other model is a full-refresh table, so only this one drifts.

I can't see the seed data, so I haven't identified the 6 specific orders. The query in section 3 lists the affected days. `raw_orders` or `raw_payments` rows for February with ids that look newly appended would be the late arrivals.

## 2. Fix

Immediate repair:

```bash
dbt run --full-refresh -s daily_revenue
```

Durable fix: with 105 orders and 69 days, incrementality saves nothing. The simplest option is to make it a plain table so it can't drift from `orders`:

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

If it must stay incremental at larger scale, drive the window from a load or updated timestamp on the source (not `max(order_date)`), use a much longer lookback, and schedule a periodic `--full-refresh`.

## 3. Test that would have caught it

`tests/assert_daily_revenue_matches_orders.sql`, a singular test that reconciles per day, so it also shows which days are wrong:

```sql
with expected as (

    select
        order_date,
        count(*) as orders,
        round(sum(amount), 2) as revenue
    from {{ ref('orders') }}
    group by order_date

),

actual as (

    select
        order_date,
        orders,
        round(revenue, 2) as revenue
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
where expected.order_date is null
   or actual.order_date is null
   or expected.orders != actual.orders
   or expected.revenue != actual.revenue
```

Running this against the current warehouse state returns the February days with the 6 missing orders and 65.00 of missing revenue. It would have failed the first run after the backfill instead of letting the dashboard silently drift.
