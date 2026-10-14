claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 74 s | USD 0.010 | 2026-10-14 | project 8ee5734 | prompt d3281e93889c

## 1. Root cause

There are two separate problems. The first is the one that produces the 474.00 vs 415.00 gap. The second explains why ops' reconciliation will still disagree after that gap is fixed.

**a. `models/daily_revenue.sql` never receives the backfilled orders.** It is incremental and only rebuilds rows where `order_date >= max(order_date) - 3 days` of the existing table. The newest row is 9 April, so the rebuild window starts 6 April. The backfilled orders are dated 31 Jan to 25 Feb, so they are never inserted into `daily_revenue`. `orders` is a full table rebuild and does contain them.

The mismatch query confirms this. The six differing days are exactly the six backfilled orders: 31 Jan (order 100), 2 Feb (101), 8 Feb (102), 13 Feb (103), 22 Feb (104) and 25 Feb (105). The differences are 6 + 12 + 9 + 15 + 20 + 3 = 65.00, which is the backfill total. The February gap (5 orders, 59.00) is that total less the 31 Jan order.

The tests pass because nothing compares `daily_revenue` to `orders`. `unique` and `not_null` cannot detect missing rows. The same bug affects any late payment that changes an old order's `amount` or `fees`: `orders` picks it up, `daily_revenue` does not.

**b. `models/staging/stg_orders.sql` derives `order_date` in UTC, but the shop works in local time.** `order_date` is `cast(ordered_at as date)`, and `ordered_at` is a naive timestamp documented as UTC. The sync screen values fit a UTC+05:30 store exactly:

| order | `ordered_at` (UTC) | +05:30 | `orders.order_date` | sync screen |
|---|---|---|---|---|
| 100 | 2018-01-31 23:18:19 | 2018-02-01 04:48:19 | 31 Jan | 1 Feb |
| 101 | 2018-02-02 20:42:44 | 2018-02-03 02:12:44 | 2 Feb | 3 Feb |
| 102 | 2018-02-08 14:00:05 | 2018-02-08 19:30:05 | 8 Feb | 8 Feb |
| 103 | 2018-02-13 07:35:51 | 2018-02-13 13:05:51 | 13 Feb | 13 Feb |
| 104 | 2018-02-22 15:47:33 | 2018-02-22 21:17:33 | 22 Feb | 22 Feb |
| 105 | 2018-02-25 19:29:02 | 2018-02-26 00:59:02 | 25 Feb | 26 Feb |

So for orders 100, 101 and 105, `orders` puts the order on a different day from the shop admin. Since the raw `ordered_at` has no timezone and `raw_orders` has no store identifier, the warehouse cannot know the store's local day. The raw data is not wrong; the model's definition is.

The offset of +05:30 is inferred from the timestamps, not confirmed. Confirm the zone name with ops.

Also check whether this store had earlier orders between 18:30 and 24:00 UTC. If it did, the admin would have disagreed before this load, and "always matched" describes coverage rather than the logic.

## 2. Fix

**a. Make `daily_revenue` a full table.** `orders` is already recomputed in full every run, and grouping it by day is cheap, so the incremental window adds risk and no benefit.

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

Then run `dbt run --select daily_revenue`. The table is rebuilt from scratch, which fixes January 31 and every February day.

**b. Derive `order_date` in shop local time.** Add the zone to `dbt_project.yml`:

```yaml
vars:
  shop_timezone: 'Asia/Kolkata'   # UTC+05:30; confirm the store's zone with ops
```

Then change the `order_date` line in `models/staging/stg_orders.sql`:

```sql
        cast(ordered_at at time zone 'UTC' at time zone '{{ var("shop_timezone") }}' as date) as order_date,
```

Update the `order_date` description in `models/schema.yml` from "Date (UTC)" to "Date in shop local time". Be aware that `customers.first_order` and `most_recent_order` also change.

Do not deploy this globally until you know the timezone of every store. `raw_orders` has no store column, so one zone would be wrong for any store elsewhere. The durable fix is to add `store_id` to the orders feed and join to a store-to-timezone mapping.

After both changes, `dbt build` should leave the reconciliation query empty. Order 100 should move into February.

I have not run any of this. I had no tools available, so the above is derived from the log and query outputs you pasted.

## 3. Tests that would have caught this

**Reconciliation test** (`tests/daily_revenue_matches_orders.sql`). It fails on the current data and would have flagged the first bad build.

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
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue

from expected
full outer join actual
    on expected.order_date = actual.order_date

where coalesce(expected.orders, 0) <> coalesce(actual.orders, 0)
   or abs(coalesce(expected.revenue, 0) - coalesce(actual.revenue, 0)) > 0.005
```

**Unit test for the timezone** (add to `models/staging/schema.yml`). It pins the two cases from the table above.

```yaml
unit_tests:
  - name: stg_orders_order_date_is_shop_local
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 100, user_id: 41, ordered_at: '2018-01-31 23:18:19', status: completed}
          - {id: 102, user_id: 15, ordered_at: '2018-02-08 14:00:05', status: completed}
    expect:
      rows:
        - {order_id: 100, order_date: '2018-02-01'}
        - {order_id: 102, order_date: '2018-02-08'}
```

Verify with `dbt test --select daily_revenue_matches_orders stg_orders` after the changes.
