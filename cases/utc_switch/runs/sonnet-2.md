claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 29 s | USD 0.074 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`raw_orders.ordered_at` (the `seeds/raw_orders.csv` load) mixes two timezone conventions, and `models/staging/stg_orders.sql` treats it as one.**

- Rows up to about mid-March hold the shop's local wall-clock time (UTC+05:30). That is why ops always reconciled cleanly.
- Rows from about mid-March onward hold UTC. Order 73 is the evidence: the admin screen shows 01:15:20 on 16 March, the warehouse has 19:45:20 on 15 March, exactly 5h30m earlier.
- The column is a naive `TIMESTAMP`, so nothing in the schema records the change.
- `stg_orders` does `cast(ordered_at as date)`, so every post-cutover order placed after 18:30 UTC (00:00 local) lands on the previous day.

Re-deriving the 26–28 March orders shown in the output sample with +5:30 reproduces the app exactly:

| Order | UTC | Local | Day |
|---|---|---|---|
| 84 | 07:04 | 12:34 | 26 |
| 85 | 11:50 | 17:20 | 26 |
| 86 | 15:45 | 21:15 | 26 |
| 87 | 21:12 | 02:42 | 27 |
| 88 | 21:51 | 03:21 | 27 |
| 89 | 18:45 | 00:15 | 28 |

That gives 26th = 3, 27th = 2, 28th = 1, matching the app (the warehouse has 5, 1, 0). The same shift moves order 82 (23 March 23:44 UTC) to 24 March locally, so 23 and 24 March also differ.

Why nothing flagged it:
- Monthly totals match because the shift only moves orders across a day boundary, not a month boundary.
- `sum(amount)` is unaffected because amounts don't depend on date.
- All 40 tests check nulls, uniqueness and accepted values, and none checks the date semantics.
- `schema.yml` documents `order_date` as "Date (UTC)", which is wrong for the early rows.

I can't see the seed, so I haven't confirmed the exact cutover row. Order 73 is the earliest affected row I know of. Check orders around 70–73 against the admin screen to pin the boundary.

## 2. Fix

Normalise to shop-local time in staging, using the cutover as a var:

```sql
-- stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- rows before the feed switched to UTC are already shop-local (UTC+05:30)
        case
            when id >= {{ var('orders_utc_from_order_id') }}
                then ordered_at + interval 330 minute
            else ordered_at
        end as ordered_at,
        cast(
            case
                when id >= {{ var('orders_utc_from_order_id') }}
                    then ordered_at + interval 330 minute
                else ordered_at
            end as date
        ) as order_date,
        status

    from source

)
```

```yaml
# dbt_project.yml
vars:
  orders_utc_from_order_id: 73   # confirm the first UTC row against the admin screen
```

Better long term is to correct the data at the source: either restate the early rows to UTC and convert everywhere, or have the feed emit one convention (ideally `timestamptz`).

Then update the `order_date` / `first_order` / `most_recent_order` descriptions to say "shop-local date".

**Rebuild `daily_revenue` with `dbt build --full-refresh`.** Its incremental filter only rewrites the last 3 days, so the wrong historical dates would otherwise stay in the table.

## 3. Tests

**Reconciliation against the admin screen.** Load ops' daily counts as a seed `seeds/admin_daily_orders.csv` (`order_date,orders`) and add `tests/assert_daily_orders_match_admin.sql`:

```sql
select
    coalesce(o.order_date, a.order_date) as order_date,
    o.orders as warehouse_orders,
    a.orders as admin_orders
from (select order_date, count(*) as orders from {{ ref('orders') }} group by 1) o
full outer join {{ ref('admin_daily_orders') }} a
    on o.order_date = a.order_date
where coalesce(o.orders, 0) <> coalesce(a.orders, 0)
```

This is the test that would have caught it, because it encodes the check ops runs by hand every morning.

**Pin the known case:**

```sql
-- tests/assert_order_73_local_date.sql
select * from {{ ref('stg_orders') }}
where order_id = 73 and ordered_at <> timestamp '2018-03-16 01:15:20'
```

A generic guard is also worth adding, since the schema can't express timezone. Something like a freshness or distribution check on `hour(ordered_at)` per week would flag a feed whose timestamps silently shift by a fixed offset, though it is noisier than the reconciliation test.
