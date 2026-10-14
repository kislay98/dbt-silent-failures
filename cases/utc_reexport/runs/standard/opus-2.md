claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 48 s | USD 0.224 | 2026-10-15 | project ad0b16b | prompt ef736a9770e3

# Diagnosis: `order_date` is the UTC day, but the admin screen counts by India day

## 1. Root cause

**`models/staging/stg_orders.sql`** derives the order day like this:

```sql
cast(ordered_at as date) as order_date,
```

In the warehouse, `raw_orders.ordered_at` is a naive `TIMESTAMP` holding **UTC** wall-clock time. The cast therefore returns the UTC calendar day. The admin screen counts by the India day (IST, UTC+05:30).

Order 73 shows the mismatch:

| | timestamp | day |
|---|---|---|
| `raw_orders.ordered_at` | 2018-03-15 19:45:20 | 15 Mar (warehouse) |
| + 5h30 → IST | 2018-03-16 01:15:20 | 16 Mar (admin) |

Any order placed between 00:00 and 05:29 IST lands on the previous day in `orders`. Order 92 is another example: 04-01 20:55 UTC is 04-02 02:25 IST.

This also explains the other symptoms:

- **Daily counts disagree but monthly totals agree.** Orders only move by one day. None of them fall in the 00:00–05:30 IST window on the 1st of a month, so no order changes month.
- **`daily_revenue` matches the admin screen for January to March, but `orders` doesn't.** `daily_revenue` is incremental and only rebuilds `max(order_date) - 3 days` on each run. Its January to March rows were written back when `order_date` still came out as the IST day. That means `raw_orders.ordered_at` used to arrive as IST local time and the ingestion job now loads it as UTC. Old rows were never rebuilt, so the stale table happens to be the "correct" one.
- **Every test passes.** Shifting a date by a day leaves keys unique and non-null and keeps accepted values valid. Nothing compares days.

The problem also reaches `customers.first_order` and `most_recent_order`. The docs in `models/schema.yml`, which say `order_date` is "Date (UTC)", describe the current wrong behaviour rather than the business definition.

## 2. Fix

In the warehouse data, `ordered_at` is now UTC. Make `stg_orders.sql` convert it explicitly instead of relying on what the loader happens to deliver:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is loaded as UTC; the business day is the India (IST) day
        cast((ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata' as date) as order_date,
        status

    from source

)
```

This uses DuckDB's ICU time zone support. India has no DST, so `cast(ordered_at + interval '5 hours 30 minutes' as date)` is an equivalent fallback.

Then rebuild the incremental model so every day uses the same definition:

```bash
dbt build --full-refresh -s stg_orders+
```

Also update the `order_date`, `first_order` and `most_recent_order` descriptions in `models/schema.yml` from "Date (UTC)" to "Date (IST)". Separately, confirm with the ingestion owners when `ordered_at` switched to UTC, so the change is documented.

Before deploying, check the result against what ops reported:

```sql
select order_date, count(*) from orders
where order_date between '2018-01-05' and '2018-01-18'
   or order_date between '2018-03-20' and '2018-03-31'
group by 1 order by 1;
```

This should match the admin figures exactly (5 Jan 2, 7 Jan 1, … 31 Mar 1), and order 73 should show `2018-03-16`.

## 3. Tests that would have caught it

**a) A unit test that fixes the day definition in place.** Add it to `models/staging/schema.yml`:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_india_day
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}  # 01:15 IST on the 16th
          - {id: 1,  user_id: 1,  ordered_at: '2018-01-01 11:38:01', status: returned}   # same day in both
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
        - {order_id: 1,  order_date: '2018-01-01'}
```

**b) A reconciliation test.** This one would have flagged the divergence on the first run after the loader changed. Save it as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with from_orders as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_daily_revenue
from from_orders o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where o.order_date is null
   or d.order_date is null
   or o.orders != d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

The two tests cover different failures. Test (a) catches a bad day definition in code. Test (b) catches cases where the incremental model and its source drift apart, whatever the cause. Here that drift came from the ingestion job silently changing the meaning of `ordered_at`.
