claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 42 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt 0a4fd749d3e5

## 1. Root cause

**`jaffle_shop/models/daily_revenue.sql`** is the problem. It is an incremental model, and its incremental filter only rebuilds the last three days:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

Only the most recent few days of April are rebuilt on each run. February rows are never rewritten, so they keep whatever values they had when they were last written. `orders` is a full `table` rebuild, so it reflects the current raw data.

The February gap fits this exactly:

| month | orders (`orders`) | orders (`daily_revenue`) | amount (`orders`) | revenue (`daily_revenue`) |
|---|---|---|---|---|
| Jan | 29 | 29 | 496 | 496 |
| **Feb** | **33** | **27** | **480** | **415** |
| Mar | 35 | 35 | 622 | 622 |
| Apr | 8 | 8 | 139 | 139 |

The gap is exactly 6 orders and $65.00, all in February. That is the 480 vs 415 and the 1737 vs 1672 totals. The gap is in whole orders, not payment amounts, so the February data changed after `daily_revenue` last covered those days, most likely through late-arriving `raw_orders` or `raw_payments` rows. The seeds reload on every run, so `orders` picked up the corrected February data immediately, but `daily_revenue` never revisits February.

The comment in the model is wrong about the mechanism. The window filters on `order_date`, not on when a payment lands, so a late payment or backfilled order dated in February is never reprocessed. The build passed because the existing tests on `daily_revenue` only check `unique` and `not_null`. Nothing compares it to `orders`.

**What I ruled out:** the cents-to-dollars conversion in `stg_payments` (the non-February months match exactly), fan-out in `region_revenue` (its revenue sums to 1737, matching `orders`), and null amounts in `orders` (the `not_null` tests pass).

I could not query the warehouse, so I have not confirmed which six orders are missing. This diagnosis rests on the figures above. To confirm, run:

```sql
select coalesce(o.order_date, d.order_date) as order_date,
       o.orders as orders_in_orders, d.orders as orders_in_daily,
       o.amount as amount_in_orders, d.revenue as revenue_in_daily
from (select order_date, count(*) as orders, sum(amount) as amount from orders group by 1) o
full outer join daily_revenue d on o.order_date = d.order_date
where o.order_date is null or d.order_date is null
   or o.orders != d.orders or abs(o.amount - d.revenue) > 0.005
order by 1;
```

Expect rows only in February.

## 2. Fix

`daily_revenue` is a 69-row aggregate. The incremental strategy saves almost nothing and is the source of the bug, so make it a plain table:

```sql
-- jaffle_shop/models/daily_revenue.sql
{{
    config(
        materialized='table'
    )
}}

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

Then rebuild it: `dbt build --select daily_revenue`. Rebuilding the table repairs February immediately. Using `--full-refresh` would also work if you keep the incremental version.

Also update the description in `jaffle_shop/models/schema.yml` so it no longer says "Incremental, rebuilds the last three days on each run". If you want to keep the incremental version, the lookback would need to cover the longest possible late arrival, and you would still need periodic full refreshes. I don't recommend it.

## 3. Test that would have caught this

Add a singular test at `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`. It fails if any order date in `daily_revenue` disagrees with `orders` in count or revenue, or if a date is missing from either side:

```sql
-- daily_revenue must agree with orders for every order date, on both order count and revenue
with from_orders as (

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
    d.orders as orders_in_daily,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_daily

from from_orders o

full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date

where o.order_date is null
   or d.order_date is null
   or o.orders != d.orders
   or abs(o.revenue - d.revenue) > 0.005
```

This test runs on every `dbt build`. It would have failed last night, naming the February dates. The existing `unique` and `not_null` tests cannot catch this kind of drift, because the rows are valid and only their values are stale.

I haven't edited any files. Say the word and I'll apply the model change, the schema description change, and the test, then run `dbt build` to confirm.
