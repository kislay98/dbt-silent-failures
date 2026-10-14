claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 71 s | USD 0.231 | 2026-10-14 | project f769fdf | prompt f8d1d1e4d74c

The shell returned no output for any command, even `echo`, so I couldn't see the seed CSVs. The analysis below uses only the files, log and query output you pasted.

## 1. Root cause: `seeds/raw_orders.csv` changed timestamp convention mid-March; `stg_orders.sql` doesn't handle it

- **Before mid-March:** `raw_orders.ordered_at` held naive shop-local wall-clock times. `stg_orders.sql` does `cast(ordered_at as date) as order_date`, so `order_date` was the local date. That is why it always matched the admin screen, even though `schema.yml` documents it as "Date (UTC)".
- **From about order 73 (16 March local) on:** the timestamps are 5h30m behind local time. Order 73 is 01:15:20 local on 16 March and `ordered_at` is 2018-03-15 19:45:20 here. So the shop is on UTC+05:30 (no DST, hence a constant offset). The feed now emits either UTC or offset-qualified strings like `2018-03-16 01:15:20+05:30`. Casting the latter to `TIMESTAMP` in DuckDB normalises it to UTC and then drops the offset, so the column type stays `TIMESTAMP` and nothing looks wrong in `information_schema`.
- **The numbers confirm it.** Adding 5:30 back to the staged rows:
  - 84, 85, 86 stay on 26 Mar (latest 21:15).
  - 87 (21:12 → 02:42) and 88 (21:51 → 03:21) move to 27 Mar.
  - 89 (18:45 → 00:15) moves to 28 Mar.
  - Result: **26 → 3, 27 → 2, 28 → 1**, exactly the admin screen.
- **Why totals and tests still pass:** orders only move across midnight, so counts, amounts and monthly totals are unchanged (1672.00) except at a month boundary. No existing test checks the time-of-day or date semantics.
- **Knock-on effects:** `orders.order_date`, `customers.first_order/most_recent_order` and `daily_revenue` all inherit the shift. `daily_revenue` is incremental and only rebuilds the last 3 days, so it needs a `--full-refresh` after the fix.
- **One thing to verify:** if every row from 73 on is shifted, 23 and 24 March should also differ (82 at 23:44 → 05:14 on the 24th: 4/2 locally vs 5/1 here). Ops only quoted 26–28. If 23–24 actually match, the cutover is between orders 83 and 84 rather than at 73. Check the raw CSV to find the first row with the new format.

## 2. Fix

Best fix: have the source emit one convention, ideally UTC with an explicit offset for every row, and backfill the old rows. Until then, normalise in staging. Pick one convention and make the docs match it.

Since ops reconciles in local time, model a true UTC instant plus a local date. First make the seed load offsets explicitly, in `dbt_project.yml`:

```yaml
seeds:
  jaffle_shop:
    raw_orders:
      +column_types:
        ordered_at: varchar   # keep the raw string, parse in staging
```

Then in `models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- older rows are naive shop-local (Asia/Kolkata), newer rows carry an offset
        case
            when regexp_matches(ordered_at, '([+-]\d{2}:?\d{2}|Z)$')
                then cast(ordered_at as timestamptz)
            else timezone('Asia/Kolkata', cast(ordered_at as timestamp))
        end as ordered_at,
        status

    from source

),

final as (

    select
        *,
        cast(timezone('Asia/Kolkata', ordered_at) as date) as order_date
    from renamed

)

select * from final
```

- If the new rows are plain UTC with no offset, branch on `order_id >= 73` (or a load date) instead of the regex.
- Change the `order_date` docs in `models/schema.yml` from "Date (UTC)" to "Date in shop local time (Asia/Kolkata)".
- Then run `dbt build --full-refresh -s daily_revenue+`, or a plain `dbt build --full-refresh`.

## 3. A test that would have caught it

Unit tests need data you'd have to hand-craft, so a reconciliation test against the app's own figures is the real guard. Short of that, a cheap data test catches this kind of drift.

**a) Singular test `tests/assert_orders_local_hour_distribution_stable.sql`.** Order times shouldn't jump wholesale. Flag weeks whose average local hour drifts far from the overall average:

```sql
with by_week as (
    select date_trunc('week', order_date) as week,
           avg(hour(timezone('Asia/Kolkata', ordered_at))) as avg_hour,
           count(*) as n
    from {{ ref('stg_orders') }}
    group by 1
)
select * from by_week
where n >= 5
  and abs(avg_hour - (select avg(avg_hour) from by_week)) > 4
```

Your weekly sample already hints at this: the week of 26 March averages 15.3h against roughly 12h elsewhere. It's noisy with only 99 rows, though.

**b) Deterministic: reconcile against the source of truth.** Load the admin screen's daily counts, for example as an `app_daily_order_counts` seed or source, and compare:

```sql
-- tests/assert_daily_counts_match_app.sql
select coalesce(a.order_date, o.order_date) as order_date, a.orders as app, o.orders as dwh
from {{ ref('app_daily_order_counts') }} a
full outer join (select order_date, count(*) as orders from {{ ref('orders') }} group by 1) o
  using (order_date)
where coalesce(a.orders, 0) <> coalesce(o.orders, 0)
```

This automates exactly the check ops does by hand, and it would have failed on 26 March.

**c) Optionally, a format guard on the raw column.** Add an `expression_is_true`-style test (dbt_utils or a singular test) asserting every `raw_orders.ordered_at` uses the same format, either all with an offset or all without. That turns a silent change in the feed's format into a failing build.
