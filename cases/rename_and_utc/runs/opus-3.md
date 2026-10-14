claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 44 s | USD 0.167 | 2026-10-14 | project f769fdf+wk6ext | prompt 3ea8d8a27b63

# Root cause: the ingestion job changed the timestamp column in `raw_orders`, and `stg_orders` didn't follow

## 1. Root cause

**What changed in the data.** From order 57 onward (2018-03-01), the ingestion job writes the order time into a new column, `order_ts`. It leaves `ordered_at` NULL for those rows. That covers 43 orders: ids 57–99, which is 35 in March and 8 in April.

**Why the dates are NULL.** `models/staging/stg_orders.sql` still builds the date from the old column only:

```sql
cast(ordered_at as date) as order_date,
```

So every March and April order gets a NULL `order_date`. That NULL flows into `orders` and into `customers` (customers 9, 12 and 93 have NULL first and most recent order dates). Amounts are unaffected, because payments join on `order_id`. That is why `sum(amount)` still reconciles at 1672.00.

**The second problem: the new column uses a different time base.**
- For Jan–Feb rows, `order_ts` equals `ordered_at` (orders 54–56). Those values are shop-local time, which is why ops' reconciliation matched every day.
- For the new rows, `order_ts` is UTC. Order 73 shows 2018-03-16 01:15:20 on the admin screen but has `order_ts` of 2018-03-15 19:45:20. That is an offset of +05:30, which matches IST (Asia/Kolkata, no daylight saving).
- So a plain `coalesce(ordered_at, order_ts)` would remove the NULLs but put late-evening orders on the wrong day. Order 73 would land on 03-15, and ops' daily reconciliation would break.
- Switching entirely to `order_ts` would not work either, because it is local time for the old rows.
- The `schema.yml` descriptions saying "Date (UTC)" are also wrong. `order_date` has always been the shop-local date.

**Why `daily_revenue` "still works".** The March and April rows in `daily_revenue` are stale. `models/daily_revenue.sql` is incremental with `where order_date >= max(order_date) - 3 days`. A NULL `order_date` never passes that filter, so this run neither deleted nor inserted anything for those days. The old rows from earlier loads, when `ordered_at` was still populated, simply survived. It will silently stop picking up new days.

**Why every test passed.** Nothing tests `order_date` for nulls. Nothing checks that `daily_revenue` agrees with `orders`.

## 2. Fix

In `models/staging/stg_orders.sql`, use `ordered_at` where it exists. Otherwise, convert the UTC `order_ts` to shop-local time:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ordered_at is shop-local; since 2018-03 ingestion leaves it null and fills order_ts in UTC instead
        coalesce(
            ordered_at,
            timezone('Asia/Kolkata', timezone('UTC', order_ts))
        ) as ordered_at,
        cast(coalesce(
            ordered_at,
            timezone('Asia/Kolkata', timezone('UTC', order_ts))
        ) as date) as order_date,
        status

    from source

)
```

If the ICU extension isn't available, `order_ts + interval '5 hours 30 minutes'` gives the same result, since IST has no daylight saving. Before relying on either version, confirm the shop's timezone and that `order_ts` is UTC with the ingestion owner. Order 73 is the only point where we can check against the admin screen.

Then rebuild, and use the stale table as a reconciliation check:

```bash
# keep the old daily_revenue: it was built from the local ordered_at, so it is the reference
duckdb <db> "create table daily_revenue_before as select * from daily_revenue"
dbt build --full-refresh -s stg_orders+
duckdb <db> "select * from daily_revenue full join daily_revenue_before using (order_date)
             where daily_revenue.orders is distinct from daily_revenue_before.orders
                or daily_revenue.revenue is distinct from daily_revenue_before.revenue"
```

The last query should return zero rows. If any day shifts, the timezone assumption is wrong. Also update the `order_date`, `first_order` and `most_recent_order` descriptions from "UTC" to the shop's local time.

## 3. Tests that would have caught it

In `models/staging/schema.yml` (`stg_orders`) and `models/schema.yml` (`orders`):

```yaml
      - name: order_date
        tests:
          - not_null
```

`tests/daily_revenue_matches_orders.sql` catches stale or missing incremental days:

```sql
with expected as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)
select *
from expected
full join {{ ref('daily_revenue') }} as actual using (order_date)
where expected.orders is distinct from actual.orders
   or expected.revenue is distinct from actual.revenue
```

A unit test pins the timezone using the order ops can see on the admin screen:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_shop_local
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 56, user_id: 79, ordered_at: '2018-02-28 10:39:12', status: completed, order_ts: '2018-02-28 10:39:12'}
          - {id: 73, user_id: 19, ordered_at: null, status: completed, order_ts: '2018-03-15 19:45:20'}
    expect:
      rows:
        - {order_id: 56, order_date: '2018-02-28'}
        - {order_id: 73, order_date: '2018-03-16'}
```

How they map to the failures:
- The `not_null` tests would have failed this run.
- The reconciliation test catches the incremental model drifting from `orders`.
- The unit test catches the subtler mistake of coalescing to UTC.
