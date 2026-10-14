claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 39 s | USD 0.113 | 2026-10-15 | project ad0b16b | prompt 63b47184243a

I only have the text you pasted, not `seeds/raw_orders.csv`, so the data-level part of the root cause is inferred. The query at the end confirms it.

## 1. Root cause

**Files:** `seeds/raw_orders.csv`, read by `models/staging/stg_orders.sql`.

`stg_orders.sql` computes `cast(ordered_at as date) as order_date`. This is correct only if `ordered_at` means what the project assumes (the docs say "Date (UTC)") and matches the app's local time. The raw feed breaks that assumption, and nothing tests it.

- **Order 73:** raw `2018-03-15 19:45:20` vs admin `2018-03-16 01:15:20`, a gap of +5h30. A naive `TIMESTAMP` carries no zone, so the cast takes the stored wall-clock date, which differs from the shop's local date for any order stored after 18:30.
- **Why the drift starts mid-March:** ops saw agreement for all of Jan to early Mar, but a fixed +5:30 offset would have broken 29 Jan already (order 28 is stored at 22:50:40). The rows from about order 71 (mid-March) onward must have a different timestamp convention from the earlier ones. The feed was probably loaded in local time up to that point, then switched to UTC. The identical offset on 26, 27 and 28 March fits this:
  - 26 Mar: 5 here vs 3 in the app, so 2 late-evening orders belong to the 27th.
  - 27 Mar: 1 vs 2, so 2 arrive and 1 leaves.
  - 28 Mar: 0 vs 1, so 1 arrives.
- **Why everything else still matches:** the orders only move between adjacent days. Monthly totals and `sum(amount) = 1672.00` can't change, and `daily_revenue` just inherits the shifted `order_date`.
- **Why the build is green:** every test is `not_null`, `unique`, `accepted_values` or `relationships`, and none checks date semantics.

One thing doesn't fit: the +5:30 offset is odd for a shop reporting in AUD with Australian regions. Sydney would be +11, so confirm the real zone with the feed owner.

## 2. Fix

Ask the feed owner for the cutover and the zone. Until the feed is corrected, normalise in staging. This assumes the cutover is by order id, so set the id to the first order on the new convention.

`dbt_project.yml`:
```yaml
vars:
  shop_timezone: 'Asia/Kolkata'      # confirm; implied by order 73 (+5:30)
  utc_feed_first_order_id: 71        # confirm with the query below
```

`models/staging/stg_orders.sql`, in the `renamed` CTE:
```sql
    select
        id as order_id,
        user_id as customer_id,
        case
            when id >= {{ var('utc_feed_first_order_id') }}
                then timezone('{{ var("shop_timezone") }}', timezone('UTC', ordered_at))
            else ordered_at
        end as ordered_at,
        cast(
            case
                when id >= {{ var('utc_feed_first_order_id') }}
                    then timezone('{{ var("shop_timezone") }}', timezone('UTC', ordered_at))
                else ordered_at
            end as date
        ) as order_date,
        status
    from source
```

Also update the `order_date` description in `schema.yml`, which wrongly says "(UTC)". `daily_revenue` rebuilds only the last 3 days, so run `dbt build --full-refresh -s daily_revenue+` afterwards.

To find the real cutover, compare against the admin screen's timestamps for orders 65 to 80. The first order whose admin time differs from the raw time is the cutover.

## 3. Tests

**Reconciliation test (catches this class of bug).** Seed ops' admin-screen daily counts as `seeds/app_daily_order_counts.csv` with columns `order_date,orders`. Then add `tests/assert_daily_orders_match_app.sql`:
```sql
select coalesce(d.order_date, a.order_date) as order_date, d.orders as dbt_orders, a.orders as app_orders
from {{ ref('daily_revenue') }} d
full outer join {{ ref('app_daily_order_counts') }} a on d.order_date = a.order_date
where coalesce(d.orders, 0) != coalesce(a.orders, 0)
```

**Unit test (pins the specific behaviour)** in `models/staging/schema.yml`:
```yaml
unit_tests:
  - name: stg_orders_uses_shop_local_date
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
```

I haven't run any of this, since the project isn't in the working directory.
