claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 50 s | USD 0.007 | 2026-10-14 | project 8ee5734 | prompt d3281e93889c

## 1. Root cause

There are two problems. The dashboard gap comes from the incremental model. The local-date mismatch comes from the data.

**a) `models/daily_revenue.sql` never revisits February.** The incremental filter only rebuilds `order_date >= max(order_date) - 3 days`. April orders are already loaded, so the window starts around 2018-04-06. The backfilled orders are dated 31 Jan to 26 Feb, so no run ever selects them and their rows in `daily_revenue` are never inserted or corrected. The output confirms this:

- The mismatched dates are exactly the six backfill dates (shifted by the timezone issue below).
- Missing rows: January is short by 1 order / 6.00, and February is short by 5 orders / 59.00. Together that is 6 orders and 65.00, matching the backfill.

The tests pass because `unique` and `not_null` check each row in isolation. Nothing compares `daily_revenue` to `orders`.

**b) The backfilled `ordered_at` values are UTC, but the shop is local.** `models/staging/stg_orders.sql` casts `ordered_at` to a date with no timezone conversion. The sync screen confirms the offset: order 100 has `ordered_at` 2018-01-31 23:18:19, and 23:18:19 + 5h30m is 2018-02-01 04:48:19, the time on the sync screen. The offset is +05:30, and the other five orders also check out:

| order | raw `ordered_at` (UTC) | +05:30 | `order_date` in `orders` | shop's local day |
|---|---|---|---|---|
| 100 | 01-31 23:18 | 02-01 04:48 | 01-31 | 1 Feb ✗ |
| 101 | 02-02 20:42 | 02-03 02:12 | 02-02 | 3 Feb ✗ |
| 102 | 02-08 14:00 | 02-08 19:30 | 02-08 | 8 Feb ✓ |
| 103 | 02-13 07:35 | 02-13 13:05 | 02-13 | 13 Feb ✓ |
| 104 | 02-22 15:47 | 02-22 21:17 | 02-22 | 22 Feb ✓ |
| 105 | 02-25 19:29 | 02-26 00:59 | 02-25 | 26 Feb ✗ |

Three of the six orders fall on the wrong day in `orders.order_date`, so ops' reconciliation would still disagree after the dashboard is fixed. `schema.yml` documents `order_date` as UTC, but ops reconciles in local time. Since the two agreed before this load, the earlier rows are probably already in local time and only the backfill is UTC. I can't confirm that from the data, so please check with the ingestion job.

## 2. Fix

**`daily_revenue`:** the model is small (one row per day), so drop the incremental config and rebuild it as a table. This is the project default and it is immune to late-arriving data of any age:

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

If you want to keep it incremental, the lookback has to cover the oldest backfill you expect, and you still need a one-off `dbt run -s daily_revenue --full-refresh`.

**Timezone:** the ingestion job should load the backfill's `ordered_at` in the same convention as the rest of the feed. If the rest is local time, convert only that store's rows in `stg_orders.sql`. I can't tell which store it is from the data, so I've left out the filter:

```sql
cast(ordered_at at time zone 'UTC' at time zone 'Asia/Kolkata' as date) as order_date
```

Don't apply this to all rows, or the existing local-time rows will shift. After either change, rebuild `orders` and `daily_revenue`.

## 3. Test that would have caught it

Add a singular test at `tests/daily_revenue_matches_orders.sql`. It fails if any day's count or revenue in `daily_revenue` differs from `orders`:

```sql
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
    order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue

from expected
full outer join actual using (order_date)

where expected.orders is distinct from actual.orders
   or abs(coalesce(expected.revenue, 0) - coalesce(actual.revenue, 0)) > 0.005
```

This test fails on tonight's build until the fix lands, which is the intended behaviour. The timezone problem can't be caught by a generic dbt test, because the warehouse doesn't know the shop's local time. It needs a reconciliation against an admin export, or a rule that each store's `ordered_at` is in a known convention.

I haven't run these queries against the warehouse. The findings are from the output you pasted, and the timezone offset is inferred from the sync screen time and the raw value.
