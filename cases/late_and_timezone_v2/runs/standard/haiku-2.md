claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 44 s | USD 0.008 | 2026-10-15 | project ad0b16b | prompt 5e3ee671b766

## 1. Root cause

**The `daily_revenue` table never receives the backfilled orders.** The problem is in `jaffle_shop/models/daily_revenue.sql`, in the incremental filter inside `{% if is_incremental() %}`:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The model rebuilds only the last three days on each run. The backfill brought in orders dated in January and February, while the table's max date is in April, so the window starts around 4 April. Every backfilled day is older than that window, so `delete+insert` never touches those days, and the old totals remain.

The numbers confirm this. Subtracting `daily_revenue` from `orders`:

| Month | `orders` | `daily_revenue` | Missing orders | Missing amount |
|---|---|---|---|---|
| Jan | 30 / 502.00 | 29 / 496.00 | 1 (order 100, coupon 6.00) | 6.00 |
| Feb | 32 / 474.00 | 27 / 415.00 | 5 | 59.00 |
| **Total** | | | **6** | **65.00** |

That is exactly the six backfilled orders ops expects. The `orders` table is a full table rebuild, so it is already correct. Only `daily_revenue` is stale.

Every test passes because none of them compares the two tables. The `unique` and `not_null` tests on `daily_revenue` check only the shape of the table, not whether its totals match `orders`.

**The timezone issue is a separate, related problem, and it's in the data.** Order 100 is stored as `2018-01-31 23:18:19` in `raw_orders`, and the store's screen shows `2018-02-01 04:48:19`. That is a +05:30 offset. The warehouse value is effectively UTC, and `stg_orders.sql` derives `order_date` with a plain `cast(ordered_at as date)`. Per `schema.yml`, `orders.order_date` is documented as "Date (UTC)", so 31 January is correct under the model's own contract. Ops reconciles against local shop time, so they see 1 February. This timezone difference does not cause the `orders` vs `daily_revenue` mismatch. It explains why ops' local-date view differs from `orders` for this store.

I inferred the +05:30 offset from one order, and I can't verify from these files whether the other stores' `ordered_at` values are UTC. That needs checking with the ingestion owner.

## 2. Fix

**Immediate (data) fix.** Rebuild the table from scratch. This is a full-refresh run, so the `is_incremental()` branch is skipped:

```bash
dbt build --select daily_revenue --full-refresh
```

Afterwards, February should read 32 orders / 474.00 and January 30 / 502.00, matching `orders`. I haven't run this here, so please confirm with the query you already use for the monthly sample.

**Code fix.** Late-arriving backfills can be any age, so no fixed lookback window can guarantee correctness. Make `daily_revenue` a plain table, which matches the project default and is cheap at this size:

```sql
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

Remove the `unique_key`, the `incremental_strategy`, and the `is_incremental()` block. Update the `daily_revenue` description in `schema.yml`, which currently says "Incremental, rebuilds the last three days on each run". If the table must stay incremental for volume reasons, you need a periodic full refresh or a check for changed days. A lookback window alone will not work.

**Timezone decision (for ops, not a code change yet).** The model documents `order_date` as UTC, so I'd keep it that way and have ops reconcile on UTC, or add a separate `order_date_local` column once every store's timezone is available in the data. I would not change `stg_orders.sql` to local time on the strength of one order. Changing the definition would move dates for every store.

## 3. Test that would have caught it

Add a singular test that reconciles the two tables by date. Save it as `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`:

```sql
with from_orders as (

    select
        order_date,
        count(*) as orders,
        coalesce(sum(amount), 0) as revenue

    from {{ ref('orders') }}

    group by order_date

),

from_daily as (

    select
        order_date,
        orders,
        revenue

    from {{ ref('daily_revenue') }}

),

compared as (

    select
        coalesce(o.order_date, d.order_date) as order_date,
        coalesce(o.orders, 0) as expected_orders,
        coalesce(d.orders, 0) as actual_orders,
        coalesce(o.revenue, 0) as expected_revenue,
        coalesce(d.revenue, 0) as actual_revenue

    from from_orders o
    full outer join from_daily d
        on o.order_date = d.order_date

)

select *
from compared
where expected_orders <> actual_orders
   or abs(expected_revenue - actual_revenue) > 0.005
```

This fails on any date where `daily_revenue` disagrees with `orders`, including dates that exist in only one of the two tables. It would have failed on the first nightly build after the backfill, rather than waiting for ops to notice a difference in February.
