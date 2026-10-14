claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 60 s | USD 0.203 | 2026-10-14 | project f769fdf | prompt 9aa546ec9ab1

# Root cause: order dates are UTC days, but the shop counts India days

## 1. Root cause

**The file is `models/staging/stg_orders.sql`**, specifically this line:

```sql
cast(ordered_at as date) as order_date,
```

**What is wrong in the data:** `raw_orders.ordered_at` is a `TIMESTAMP` with no time zone, and the values in it are **UTC**. Casting it straight to a date gives the UTC day. The admin screen shows the India day (IST, UTC+05:30). So any order placed between 18:30 and 23:59 UTC is counted one day too early in `orders`.

The samples confirm this:

| order | `ordered_at` (UTC) | IST | warehouse day | admin day |
|---|---|---|---|---|
| 73 | 03-15 19:45:20 | **03-16 01:15:20** | 15th | 16th (matches what ops reported) |
| 16 | 01-17 21:07:25 | 01-18 02:37 | 17th | 18th |
| 82 | 03-23 23:44:48 | 03-24 05:14 | 23rd | 24th |
| 87, 88 | 03-26 21:12 / 21:51 | 03-27 | 26th | 27th |
| 89 | 03-27 18:45:59 | 03-28 00:15 | 27th | 28th |
| 12 | 01-15 10:13:37 | 01-15 15:43 | 15th | 15th (correct, before 18:30 UTC) |

Moving every order at or after 18:30 UTC forward one day reproduces the admin counts exactly for both windows:
- **January:** the 6th, 10th and 13th move to the 7th, 11th and 14th, and one order moves from the 17th to the 18th.
- **March:** 23rd→24th, 26th→27th twice, and 27th→28th.

**Why monthly totals still agree:** none of the late-UTC orders fell on the last evening of a month. That is luck, not correctness.

**Why `daily_revenue` matches the admin screen but not `orders`:** `daily_revenue` is incremental and only rebuilds the last 3 days on each run. Its January to early-April rows were written while `order_date` was still the IST day and have never been recomputed. This means something changed upstream. Either `stg_orders` used to convert the time zone, or the ingestion job switched `ordered_at` from IST to UTC. Check the history of `stg_orders.sql` and of the ingestion job to find out which. The 5 `NULL` rows in the reconciliation are IST-only dates (Jan 7, Jan 11, Feb 8, Mar 16, Mar 28). Those dates have no orders on the UTC side.

**Why every test passed:** nothing checks what `order_date` means. Also, `schema.yml` documents `order_date` as "Date (UTC)", so the docs describe the bug as if it were intended.

## 2. Fix

In `models/staging/stg_orders.sql`:

```sql
    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is UTC; the business day is the India (Asia/Kolkata) local day
        cast((ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata' as date) as order_date,
        status
```

This needs DuckDB's ICU extension, which is bundled in standard builds. If ICU isn't available, `cast(ordered_at + interval '5 hours 30 minutes' as date)` is equivalent, because India has no daylight saving time.

Then:
- Change the `order_date` descriptions in `models/schema.yml` from "Date (UTC)" to "Date (IST, Asia/Kolkata)". Do the same for `first_order` and `most_recent_order` on `customers`.
- Rebuild the incremental model so its history is computed with the same logic:
  ```
  dbt build --full-refresh -s stg_orders+
  ```
  Without the full refresh, the last few days in `daily_revenue` stay on UTC dates while older days are IST.

## 3. Tests that would have caught it

**Unit test on the date logic.** Add this to `models/staging/schema.yml`; unit tests are supported in dbt 1.8+. It pins the 18:30 UTC boundary and the reported example, order 73:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_india_local_day
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 1,  user_id: 1,  ordered_at: "2018-03-15 18:29:59", status: placed}
          - {id: 2,  user_id: 1,  ordered_at: "2018-03-15 18:30:00", status: placed}
          - {id: 73, user_id: 19, ordered_at: "2018-03-15 19:45:20", status: completed}
          - {id: 4,  user_id: 1,  ordered_at: "2018-03-31 20:00:00", status: placed}
    expect:
      rows:
        - {order_id: 1,  order_date: "2018-03-15"}
        - {order_id: 2,  order_date: "2018-03-16"}
        - {order_id: 73, order_date: "2018-03-16"}
        - {order_id: 4,  order_date: "2018-04-01"}
```

**Reconciliation test** in `tests/assert_daily_revenue_matches_orders.sql`. This would have failed as soon as the two models diverged:

```sql
with o as (
    select order_date, count(*) as orders from {{ ref('orders') }} group by 1
)
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as dashboard_orders
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.orders is distinct from d.orders
```

The unit test catches the time zone mistake itself. The reconciliation test catches any future logic change that the incremental model doesn't pick up.
