claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 29 s | USD 0.098 | 2026-10-15 | project ad0b16b | prompt 11fb6c29d933

## 1. Root cause

**`models/staging/stg_orders.sql`** derives the date from a column that is now empty for new rows. The ingestion job changed the shape of `raw_orders`. It added `order_ts`, and for the March/April load it filled only that column and left `ordered_at` NULL. The raw table has 43 rows with `ordered_at` NULL and `order_ts` populated, for example order 63 (`2018-03-06 12:28:52`) and order 96 (`2018-04-06 13:16:25`).

The failure then spreads silently:

- `stg_orders` computes `cast(ordered_at as date)`, so `order_date` is NULL for those 43 orders.
- `orders` therefore has `order_date = NULL` for 43 rows. Those rows still carry their amounts, so `sum(amount)` is 1672.00 in both tables and the totals reconcile.
- Nothing tests `order_date`. `schema.yml` has no `not_null` on `orders.order_date` or on `stg_orders.order_date`, so the build stays green.
- `daily_revenue` is incremental and only re-reads `order_date >= max(order_date) - 3 days`. That filter also drops NULLs, so March/April stay as they were from earlier runs. The `not_null` and `unique` tests on `daily_revenue` pass on stale data.
- `customers.first_order` and `most_recent_order` are NULL for the same orders (only 42 of 100 customers have a `first_order`).

## 2. Fix

In `stg_orders.sql`, take the timestamp from whichever column is populated:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        coalesce(ordered_at, order_ts) as ordered_at,
        cast(coalesce(ordered_at, order_ts) as date) as order_date,
        status

    from source

)
```

Then rebuild, because the incremental model won't heal itself:

```
dbt build --full-refresh -s stg_orders+ 
```

I can't see order 73's `order_ts`, and that row decides whether the plain `coalesce` is enough. Check it after the rebuild:

- If `order_date` for order 73 is `2018-03-16`, the fix is complete. The admin screen shows the shop's local time, and `order_ts` is then in that same time.
- If it comes out as `2018-03-15`, the new column is UTC and must be converted to shop time before taking the date. Use `cast(coalesce(ordered_at, order_ts at time zone 'UTC' at time zone '<shop tz>') as date)`. The raw tables don't say which Australian timezone the shop uses, so confirm it. Sydney and Melbourne are on +11 until 2018-04-01 and +10 after, so use a named zone, not a fixed offset.

Also check that the monthly report returns January through April and that `count(*) where order_date is null` is 0. The `order_date` descriptions in `schema.yml` say "UTC". Update them to match whatever the check shows.

## 3. Tests that would have caught it

Add to `models/staging/schema.yml` and `models/schema.yml`:

```yaml
      - name: order_date
        tests:
          - not_null
```

on both `stg_orders` and `orders`. This alone fails the build with 43 failures.

Add a singular test, `tests/daily_revenue_matches_orders.sql`, to catch the incremental model drifting from its source:

```sql
select
    (select count(*) from {{ ref('orders') }}) as order_rows,
    (select sum(orders) from {{ ref('daily_revenue') }}) as daily_rows,
    (select sum(amount) from {{ ref('orders') }}) as order_amount,
    (select sum(revenue) from {{ ref('daily_revenue') }}) as daily_amount
where order_rows != daily_rows
   or abs(order_amount - daily_amount) > 0.005
```

This fails when `orders` has NULL-dated rows that `daily_revenue` drops. It also fails if `daily_revenue` goes stale. Run it after `--full-refresh` too, since it only passes when the two agree.
