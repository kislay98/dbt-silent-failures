claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 28 s | USD 0.073 | 2026-10-14 | project f769fdf+wk6ext | prompt 4d613ca19f07

## 1. Root cause

**`raw_orders` (ingestion job) changed which column it fills, and `models/staging/stg_orders.sql` still reads the old one.**

- From 2018-03-01 on (order 57 onward, 43 orders, 35 in March and 8 in April), the loader leaves `ordered_at` NULL and fills only the new `order_ts` column.
- `stg_orders` does `cast(ordered_at as date) as order_date`, so those 43 orders get a NULL `order_date`.
- `orders` therefore has 43 NULL-month rows, and the monthly report shows only Jan and Feb. The 43 rows (761.00) plus 496 and 415 add up to the 1672.00 total, so no amounts were lost.
- `customers` is hit too: customers 9, 12 and 93 have NULL `first_order` and `most_recent_order`, and customer 8 is missing one order from those dates.

**The two columns are not in the same timezone.** For Jan and Feb rows, `order_ts` equals `ordered_at` (a backfill copy of local time). For order 73, `order_ts` is 2018-03-15 19:45:20 while the admin screen shows 2018-03-16 01:15:20, a gap of exactly +5:30. So new `order_ts` values look like UTC, while the old `ordered_at` was shop-local time (that is why it reconciled with the admin screen). Simply swapping to `cast(order_ts as date)` would move late-evening orders, such as order 73, to the wrong day.

**Why nothing failed and `daily_revenue` looks fine:**
- No test covers `order_date`: `orders.order_date` and `stg_orders.ordered_at` have no `not_null`, and the `not_null` and `unique` tests on `daily_revenue.order_date` can't see the problem.
- `daily_revenue` is incremental with `where order_date >= max(order_date) - 3 days`. That filter drops NULL dates, and it only rebuilds the last 3 days. The March and April rows are stale leftovers from earlier runs, built when `ordered_at` was still populated.

## 2. Fix

In `models/staging/stg_orders.sql`, use `ordered_at` when present and otherwise convert `order_ts` from UTC to shop time:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        coalesce(ordered_at, order_ts + interval 330 minute) as ordered_at,
        cast(coalesce(ordered_at, order_ts + interval 330 minute) as date) as order_date,
        status

    from source

)
```

- 330 minutes comes from the 01:15:20 vs 19:45:20 gap on order 73. It matches IST, which has no DST. Please confirm that `order_ts` is UTC and that the shop is on IST with the ingestion owners. A `var` for the offset or `convert_timezone` would also work.
- Do not use `order_ts` alone, because Jan/Feb values are already local.
- Fix the `order_date` descriptions in `schema.yml` (and in `customers`) from "Date (UTC)" to "Date in shop local time".

Then rebuild `daily_revenue` from scratch. Some orders change dates (order 73 moves to 03-16), and the 3-day window would never correct the stale rows:

```
dbt build --full-refresh -s stg_orders+
```

Afterwards, the monthly report should show 29 / 27 / 35 / 8 orders for Jan–Apr (the exact March and April split may shift slightly from the UTC-to-local conversion). Order 73 should have `order_date` 2018-03-16.

Also ask ops to have the loader populate one agreed column, or document that `order_ts` is UTC.

## 3. Tests

In `models/staging/schema.yml`, under `stg_orders`:

```yaml
      - name: order_date
        tests:
          - not_null
```

In `models/schema.yml`, add the same `not_null` to `orders.order_date`.

Add a singular test, `tests/assert_daily_revenue_matches_orders.sql`, so a stale incremental table is caught as well:

```sql
with o as (
    select count(*) as n, sum(amount) as amt from {{ ref('orders') }}
),
d as (
    select sum(orders) as n, sum(revenue) as amt from {{ ref('daily_revenue') }}
)
select *
from o cross join d
where o.n != d.n or abs(o.amt - d.amt) > 0.01
```

Optionally, add a stg_orders check that every raw row has some timestamp, so the next loader change is caught at the source:

```sql
select * from {{ ref('raw_orders') }}
where ordered_at is null and order_ts is null
```

With the `not_null` tests in place, this load would have failed the build on `stg_orders.order_date` with 43 failures instead of passing.
