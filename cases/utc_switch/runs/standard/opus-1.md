claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 48 s | USD 0.223 | 2026-10-15 | project ad0b16b | prompt 63b47184243a

# Root cause: shifted `ordered_at` timestamps in `raw_orders`

## 1. Root cause

**The data is wrong, in `seeds/raw_orders.csv`.** From mid-March onward, the `ordered_at` values are recorded in a different timezone from the earlier rows. Each one is **5 h 30 m earlier** than the shop-local time the admin screen shows.

- Order 73: the admin screen shows `2018-03-16 01:15:20`, but `raw_orders.ordered_at` is `2018-03-15 19:45:20`. That is a difference of −05:30.
- `stg_orders.sql` passes the value through and does `cast(ordered_at as date) as order_date`. Any order placed between 00:00 and 05:30 local time therefore gets the previous day's date. That date flows into `orders`, `daily_revenue` and `customers.first_order`/`most_recent_order`.
- The reported mismatches fit this exactly. Early-morning orders on the 27th and 28th move back a day: the 26th gains 2 (5 vs 3), the 27th loses 2 and gains 1 (1 vs 2), and the 28th loses 1 (0 vs 1). Order 88 (`2018-03-26 21:51:20` here, which is 03:21 on the 27th locally) is one of them.
- Monthly totals and `sum(amount) = 1672.00` still match. No shifted order crosses a month boundary, and amounts are untouched. Only the day buckets are wrong.
- No test fails because nothing checks timestamps. The types (`TIMESTAMP`, no offset) are identical before and after the change.

The earlier rows agreed with the app, so they effectively carry shop-local time. That means the `schema.yml` descriptions saying "Date (UTC)" are also inaccurate. You'll want to decide which convention is correct, but the immediate bug is that the feed changed convention partway through.

I don't have the seed CSV, so I can't name the exact first affected row. Order ids are sequential, so it is every id from some cutover N (≤ 73) onward. Two ways to find N:

```sql
-- the cutover usually shows as ordered_at stepping backwards, or as a shift in the hour-of-day profile
select id, ordered_at,
       ordered_at - lag(ordered_at) over (order by id) as gap,
       hour(ordered_at) as hr
from raw_orders
where ordered_at >= '2018-03-01'
order by id;
```

You can also confirm N against the admin screen for a few orders in early to mid March.

## 2. Fix

**Preferred: fix the data at the source.** Get the export corrected so every row uses one convention, ideally with an explicit offset. Then correct the affected rows in `seeds/raw_orders.csv` so they match the earlier rows:

```sql
-- one-off correction, then write back to seeds/raw_orders.csv
copy (
  select id, user_id,
         case when id >= :cutover_id then ordered_at + interval 5 hour + interval 30 minute
              else ordered_at end as ordered_at,
         status
  from raw_orders order by id
) to 'seeds/raw_orders.csv' (header);
```

If you can't change the source yet, add a stopgap in `models/staging/stg_orders.sql`:

```sql
        case when id >= {{ var('orders_tz_cutover_id') }}
             then ordered_at + interval 5 hour + interval 30 minute
             else ordered_at end as ordered_at,
        cast(case when id >= {{ var('orders_tz_cutover_id') }}
                  then ordered_at + interval 5 hour + interval 30 minute
                  else ordered_at end as date) as order_date,
```

**Then rebuild with `--full-refresh`.** `daily_revenue` is incremental and only rebuilds the last 3 days before its max date (early April). A normal `dbt build` would leave the wrong mid/late-March rows in place:

```
dbt seed && dbt build --full-refresh
```

After that, check that 26/27/28 March read 3/2/1 and that order 73 has `order_date = 2018-03-16`.

## 3. A test that would have caught it

**Definitive check: reconcile against the app.** Load the app's daily counts (or an admin export with local timestamps) as a seed or source. Then add a singular test, `tests/assert_daily_orders_match_app.sql`:

```sql
select coalesce(d.order_date, a.order_date) as order_date, d.orders, a.orders as app_orders
from {{ ref('daily_revenue') }} d
full outer join {{ ref('app_daily_order_counts') }} a using (order_date)
where coalesce(d.orders, 0) <> coalesce(a.orders, 0)
```

This automates the reconciliation ops already does by hand, and it would have failed on 26–28 March.

**Cheap check with no external data:** order ids are assigned in time order, so timestamps should never go backwards. Add `tests/assert_ordered_at_monotonic.sql`:

```sql
select id, ordered_at, prev_ordered_at
from (
  select id, ordered_at, lag(ordered_at) over (order by id) as prev_ordered_at
  from {{ ref('raw_orders') }}
)
where ordered_at < prev_ordered_at
```

This catches a backward step at the cutover whenever consecutive orders are less than 5.5 h apart. It isn't guaranteed to catch every case, which is why the reconciliation test is the one to rely on.

Longer term, have the feed send `ordered_at` with an explicit offset and load it as `timestamptz` (set `column_types` in the seed config). Then derive `order_date` explicitly in the shop's timezone, e.g. `cast(timezone('Australia/…', ordered_at) as date)`, and fix the "(UTC)" descriptions to match.
