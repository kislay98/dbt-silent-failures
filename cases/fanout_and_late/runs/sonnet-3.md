claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 23 s | USD 0.068 | 2026-10-14 | project 8ee5734 | prompt 89287ed414a2

## 1. Root cause

`models/daily_revenue.sql` is incremental, and its rebuild window is anchored to the wrong thing. The filter is:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`max(order_date)` is the newest order in the table, which is in April 2018. So each run only rebuilds the last 3 days of April and never touches earlier dates.

The backfilled February orders have old `order_date`s but arrived in last night's load. `orders` is a full-rebuild table, so it picked them up (33 orders, 480.00). `daily_revenue` kept the February rows it computed before the backfill (27 orders, 415.00).

The six affected days in your diff add up exactly to the gap:

| Date | Missing orders | Missing revenue |
|---|---|---|
| 02-03 | 1 | 12 |
| 02-08 | 1 | 9 |
| 02-13 | 1 | 15 |
| 02-17 | 1 | 6 |
| 02-22 | 1 | 20 |
| 02-26 | 1 | 3 |
| **Total** | **6** | **65** |

The comment about payments landing a day or two late describes a different case, and the 3-day lookback does not cover a month-long outage. Every test passes because none of them compare `daily_revenue` against `orders`. `unique` and `not_null` on `order_date`, `orders` and `revenue` are all satisfied by stale rows.

## 2. Fix

**One-off data fix.** Rebuild the table from scratch:

```
dbt build --full-refresh -s daily_revenue
```

**Permanent fix.** The raw tables have no `loaded_at` or `updated_at` column to key an incremental on, so an incremental can't reliably detect late arrivals. The aggregate is tiny, so make it a plain table. In `daily_revenue.sql`:

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

Also update the model description in `schema.yml`, which still says it rebuilds the last three days. If you need to keep it incremental later, filter on a load timestamp from the source rather than on `max(order_date)`.

## 3. Test that would have caught it

Add `tests/assert_daily_revenue_matches_orders.sql`. It returns rows on any mismatch, so it fails the build:

```sql
select
    o.order_date,
    count(*) as in_orders,
    coalesce(d.orders, 0) as in_daily_revenue,
    round(sum(o.amount), 2) as orders_amount,
    coalesce(d.revenue, 0) as daily_revenue
from {{ ref('orders') }} o
left join {{ ref('daily_revenue') }} d using (order_date)
group by 1, 3, 5
having count(*) <> coalesce(d.orders, 0)
    or abs(sum(o.amount) - coalesce(d.revenue, 0)) > 0.005
```

Run against last night's data, it would have returned exactly the six rows in your diff. If `daily_revenue` ever had dates that `orders` lacks, you could add the reverse direction with a `full outer join`.

## Separate issue: `region_revenue` looks overstated

`region_revenue` sums to 1838.00 across regions, but `orders` totals 1737.00, a 101.00 difference. Order counts match at 105, which is consistent with fan-out, since they use `count(distinct)` while revenue uses `sum`.

The likely cause is that `raw_customer_regions` has an `updated_at` column, so it probably holds more than one row per customer despite the "one row per customer" description. The join to `orders.customer_id` then duplicates payments. Nothing tests `customer_id` for uniqueness there.

I can't see the seed data, so this is unconfirmed. Check with:

```sql
select customer_id, count(*) from raw_customer_regions group by 1 having count(*) > 1
```

If it confirms, keep the latest row per customer in `region_revenue` and add a `unique` test on `raw_customer_regions.customer_id`. The same check applies to `raw_payment_methods.payment_method`, since a duplicate there would inflate `orders.amount`.
