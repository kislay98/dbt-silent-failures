claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 52 s | USD 0.226 | 2026-10-15 | project ad0b16b | prompt 15c0aae6d239

# Orders lost their `order_date` because the raw feed moved the timestamp to a new column

## 1. Root cause

**What changed in the data.** `raw_orders` now has a new column, `order_ts`. For the 43 orders from March and April, the ingestion job filled `order_ts` and left `ordered_at` NULL. For January and February, `ordered_at` is still filled. In the sampled rows, `order_ts` equals `ordered_at` for those older orders (e.g. order 25).

**Where it breaks.** In `jaffle_shop/models/staging/stg_orders.sql`, `order_date` is built only from the old column:

```sql
cast(ordered_at as date) as order_date,
```

So every order from March onwards gets `order_date = NULL`. The amounts still add up (1672.00 in both places) because only the date is lost. The NULL month row in your report is 43 orders and 761.00, which is exactly March plus April in `daily_revenue` (35 + 8 orders, 622 + 139).

**Why no test failed.**
- Nothing tests `order_date` for NULLs: not in `stg_orders`, `orders` or `customers`.
- `daily_revenue` hides the problem. Its incremental filter `where order_date >= max(order_date) - 3 days` drops the NULL rows. So `delete+insert` replaces nothing, and the old March/April rows stay as they were. The table looks right only because it is stale. From now on it will never pick up a new day.
- `customers.first_order` and `most_recent_order` are also NULL for customers whose orders are all from March onwards (e.g. customers 9, 47 and 21).

**A second problem: `order_ts` is not in the shop's local time.** Order 73 is 2018-03-16 01:15:20 on the admin screen but 2018-03-15 19:45:20 in `order_ts`. That is 5h30m earlier, and here it lands on the previous day. So `coalesce(ordered_at, order_ts)` would remove the NULLs but put late-evening orders on the wrong day, and the morning reconciliation would fail.

A 5h30m gap is not what UTC would give for an Australian shop (that would be 10–11h). It could be a fixed IST offset compared with Sydney summer time. If so, the gap would become 4h30m after Australian daylight saving ended on 2018-04-01. One order is not enough to tell, so check the offset before relying on it. Also, the `schema.yml` description "Date (UTC)" is wrong: `order_date` has always matched the shop's local date.

## 2. Fix

**Step 1: back up `daily_revenue` before any rebuild.** It is the only copy of the correct March/April dates:

```sql
create table daily_revenue_backup as select * from daily_revenue;
```

**Step 2: find the right conversion by testing it against that copy.** This query should return no rows:

```sql
with fixed as (
    select cast(order_ts + interval 330 minute as date) as order_date, count(*) as orders
    from raw_orders
    where ordered_at is null
    group by 1
)
select coalesce(f.order_date, d.order_date) as order_date, f.orders as fixed, d.orders as expected
from fixed f
full join daily_revenue_backup d on f.order_date = d.order_date
where coalesce(f.order_date, d.order_date) >= date '2018-03-01'
  and f.orders is distinct from d.orders
```

- If days after 2018-04-01 don't match, the offset follows daylight saving. Use a time-zone conversion instead of a fixed interval, for example `(order_ts at time zone 'Asia/Kolkata') at time zone 'Australia/Sydney'`. Use whatever zones make the comparison come out clean.
- Also confirm the older rows agree: `select count(*) from raw_orders where ordered_at is not null and order_ts is distinct from ordered_at` should be 0.

**Step 3: update `stg_orders.sql`** with the conversion that passed:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- the feed moved to order_ts from March 2018; order_ts is 5h30m behind the shop's local time
        coalesce(ordered_at, order_ts + interval 330 minute) as ordered_at,
        cast(coalesce(ordered_at, order_ts + interval 330 minute) as date) as order_date,
        status

    from source

)
```

**Step 4: rebuild and update the docs.**
- Run `dbt build --full-refresh -s daily_revenue+` after the fix, so `daily_revenue` is rebuilt from the corrected `orders`.
- Change the `order_date`, `first_order` and `most_recent_order` descriptions from "(UTC)" to "shop local time".
- Ask the ingestion owner whether `ordered_at` is permanently gone and what time zone `order_ts` is in.

## 3. Tests that would have caught this

Add `not_null` to `order_date` in `models/staging/schema.yml` (`stg_orders`) and in `models/schema.yml` (`orders`):

```yaml
      - name: order_date
        tests:
          - not_null
```

Add a reconciliation test so `daily_revenue` can't quietly fall behind `orders`. Save it as `tests/daily_revenue_matches_orders.sql`:

```sql
-- every order must be counted in daily_revenue on the same order_date
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
)
select *
from o
full join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.order_date is null
   or d.order_date is null
   or o.orders <> d.orders
   or abs(o.revenue - d.revenue) > 0.005
```

On this load:
- `not_null` would fail with 43 rows.
- The reconciliation test would fail because of the NULL-date group.
