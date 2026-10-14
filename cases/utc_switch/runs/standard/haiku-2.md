claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 41 s | USD 0.007 | 2026-10-15 | project ad0b16b | prompt 63b47184243a

**Caveat:** I haven't run anything. This is based on the files, log, and sample outputs you provided. The seed data isn't included, so I can't confirm the exact row-level cause.

## 1. Root cause

**Primary file: `jaffle_shop/models/staging/stg_orders.sql`**, in the line `cast(ordered_at as date) as order_date`.

`ordered_at` is a naive `TIMESTAMP` (`raw_orders.ordered_at`, per the columns output), and `schema.yml` documents `order_date` as "Date (UTC)". The model truncates that UTC timestamp to a date without converting it to the shop's local zone. The admin screen shows local time, so the two disagree whenever the local date differs from the UTC date. `orders.sql`, `customers.sql` (`first_order`, `most_recent_order`) and `daily_revenue.sql` all inherit the wrong date from here.

The sample data fits this:

- Order 73: stored `2018-03-15 19:45:20`, admin `2018-03-16 01:15:20`. The offset is +05:30.
- Orders that cross local midnight move to the next day, which is why the counts shift forward one day at a time (26 March loses orders, 27 March gains one, 28 March gains one).
- Monthly totals still match because only the day boundary moves, not the orders.

Two things don't fit a simple "UTC vs. Australian local time" story, and you need to resolve them before choosing a fix:

- **The +05:30 offset is not an Australian offset.** Eastern Australia is +10:00 or +11:00 during DST. Either the admin screen's timezone is set to something other than the shop's, or `ordered_at` isn't plain UTC for every row.
- **The two systems agreed until mid-March.** A constant UTC-to-local offset would have produced disagreements all along. So something changed around that date, most likely in how `raw_orders.ordered_at` is written (a source change, a re-export, or a seed reload that mixed zones). The bad data is probably in the rows loaded after the change. Check with:

```sql
select date_trunc('day', ordered_at) as d, min(ordered_at), max(ordered_at), count(*)
from raw_orders
where ordered_at between '2018-03-10' and '2018-03-20'
group by 1 order by 1;
```

Also ask ops for the admin screen's timezone setting and a few order IDs with their admin timestamps, so you can see where the offset changes.

## 2. Fix

**a. Confirm the shop's timezone with ops first.** Don't assume Australia/Sydney, given the +05:30 result.

**b. Convert in staging, in one place.** Replace the cast in `stg_orders.sql`. Use a var so the zone isn't hardcoded (the zone below is a placeholder until (a) is confirmed):

```sql
-- dbt_project.yml: vars: { shop_timezone: 'Australia/Sydney' }  -- confirm first
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(
            timezone('{{ var("shop_timezone") }}', timezone('UTC', ordered_at))
            as date
        ) as order_date,
        status

    from source

)
```

Keep `ordered_at` as is, and update the `order_date` descriptions in `models/schema.yml` from "Date (UTC)" to "Date (shop local time)". Otherwise the docs will keep contradicting the data.

**c. Rebuild, including `daily_revenue` with a full refresh.** The incremental model only rebuilds the last three days, and `delete+insert` only removes rows for dates it is rebuilding. Older rows already counted under the wrong dates would stay. Run:

```
dbt build --full-refresh -s stg_orders+
```

This rebuilds `daily_revenue` from scratch and the downstream tables normally.

**d. If the bad rows are in the source,** the stored `ordered_at` values after the change need correcting at the source or in the seed, not just in the model. Step (b) only works if the timestamps all share one convention.

## 3. A test that would have caught this

All the existing tests are structural (`unique`, `not_null`, `accepted_values`, `relationships`). None of them check values, and the monthly totals couldn't reveal a day-boundary shift. The test has to compare against something independent of the model's own assumptions. Add a daily-count reconciliation against the admin screen's numbers:

1. Export daily order counts from the admin screen into a seed, e.g. `seeds/admin_daily_order_counts.csv` with `order_date, orders`.
2. Add a singular test, `tests/assert_daily_orders_match_admin.sql`:

```sql
with admin as (

    select * from {{ ref('admin_daily_order_counts') }}

),

warehouse as (

    select order_date, count(*) as orders
    from {{ ref('orders') }}
    group by 1

)

select
    coalesce(a.order_date, w.order_date) as order_date,
    a.orders as admin_orders,
    w.orders as warehouse_orders
from admin a
full outer join warehouse w
    on a.order_date = w.order_date
where a.orders is distinct from w.orders
```

A test that recomputes the local date from `ordered_at` with the same zone would be weaker: it shares the model's assumption, so it passes if that assumption is wrong. The reference-data comparison is what catches a spec mismatch like this one.

Once the reference seed exists, the test will flag the 26–28 March rows (and any others) until the fix is in.
