claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 46 s | USD 0.215 | 2026-10-15 | project ad0b16b | prompt 5e3ee671b766

# Two separate problems explain the February mismatch

The numbers reconcile exactly once both problems are separated:

| | `orders` | `daily_revenue` | Difference |
|---|---|---|---|
| Jan | 30 / 502.00 | 29 / 496.00 | 1 order, 6.00 (order 100, the 600-cent coupon payment) |
| Feb | 32 / 474.00 | 27 / 415.00 | 5 orders, 59.00 |
| **Total** | | | **6 orders, 65.00**, which is exactly the backfill |

## 1. Root cause

### A. `models/daily_revenue.sql`: the incremental filter drops late-arriving rows

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

- On each run, only the last 3 days (around April 2018) are rebuilt.
- The six backfilled orders 100–105 are dated 31 Jan to 26 Feb, so they never pass the filter.
- `delete+insert` never touches those dates, so the old pre-backfill rows stay in place.
- `orders` is a full `table` rebuild, so it does include them. That is the 27 vs 32 and 415.00 vs 474.00 gap.
- Every test passes because none of them compare `daily_revenue` to `orders`.

### B. `raw_orders`: the backfilled rows have timestamps in a different timezone

- Order 100 is `2018-02-01 04:48:19` on the store's screen (shop local time) but `2018-01-31 23:18:19` in `raw_orders`. That is 5h30m earlier.
- The shift isn't limited to order 100. Order 101 appears in `orders` on **2018-02-02**, but the store screen lists the second order on **3 Feb**.
- The existing rows match the shop admin by day, so they are stored in shop local time. The backfill export used a different offset; it looks like the sync host's UTC+05:30.
- `stg_orders.sql` just does `cast(ordered_at as date)`. So any row whose local time is before 05:30 lands on the previous day.
- Order 105 (screen: 26 Feb) should be checked too.
- Side note: `schema.yml` documents `order_date` as "Date (UTC)". Given that ops reconciles successfully against local time, that description is wrong. It should say shop local time.

## 2. Fix

### Data fix (B)

The proper fix is to have ingestion re-export the store's backfill in shop local time, like every other row, and reload it.

If you need a stopgap before the re-export, first verify that the 5h30m offset holds for all six rows against the sync screen:

```sql
select id, ordered_at, ordered_at + interval '5 hours 30 minutes' as local_ts
from raw_orders where id between 100 and 105 order by id;
-- expect local dates 1, 3, 8, 13, 22, 26 Feb; order 100 → 2018-02-01 04:48:19
```

If they match, patch the rows:

```sql
update raw_orders
set ordered_at = ordered_at + interval '5 hours 30 minutes'
where id between 100 and 105;
```

Note that the ingestion job may overwrite this patch on its next load. That is another reason the re-export is the real fix.

### Code fix (A)

`daily_revenue` has only 69 rows, and `raw_orders` has no `loaded_at` column to drive a correct incremental. The incremental model buys nothing and silently loses backfills, so make it a plain table:

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

Also update its description in `schema.yml`, which currently says it "rebuilds the last three days on each run".

If you need to keep it incremental, the ingestion job must add a `loaded_at` column to `raw_orders`. The filter would then be "rebuild every `order_date` that has rows loaded since the last run", not "the last 3 days". Until then, run `dbt build --full-refresh -s daily_revenue` after any backfill.

### Expected result

After both fixes, run `dbt build` (with `--full-refresh -s daily_revenue+` if you keep it incremental). Both tables should then show:

- **Jan:** 29 orders / 496.00
- **Feb:** 33 orders / 480.00

That is February's original 27 / 415.00 plus the full backfill of 6 / 65.00.

## 3. Tests that would have caught this

### Reconciliation test (catches A)

Add `tests/daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must agree with orders for every order_date
with expected as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(e.order_date, d.order_date) as order_date,
    e.orders  as expected_orders,  d.orders  as actual_orders,
    e.revenue as expected_revenue, d.revenue as actual_revenue
from expected e
full outer join {{ ref('daily_revenue') }} d
    on e.order_date = d.order_date
where e.orders is distinct from d.orders
   or e.revenue is distinct from d.revenue
```

Last night this would have failed on 31 Jan, 1 Feb, 3 Feb (current dating), 8, 13, 22 and 26 Feb.

### Timezone check (B)

The reconciliation test won't catch B, because both models inherit the same wrong dates. To catch it automatically:

- Have ingestion load `ordered_at` as `TIMESTAMPTZ`, or add a store/offset column.
- Add a source test that every row carries the expected offset.

Until then, the only check for B is ops' daily comparison against the shop admin.
