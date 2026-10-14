claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 55 s | USD 0.224 | 2026-10-14 | project ad0b16b | prompt 150d20052204

# Diagnosis: February mismatch between `daily_revenue` and `orders`

There are two separate defects. Together they account for the whole gap.

## 1. Root cause

**Reconciling the numbers first.** The gap between `orders` and `daily_revenue` is not only in February:

| month | `orders` | `daily_revenue` | missing |
|---|---|---|---|
| Jan | 30 / 502.00 | 29 / 496.00 | 1 order / 6.00 |
| Feb | 32 / 474.00 | 27 / 415.00 | 5 orders / 59.00 |
| total | | | **6 orders / 65.00** |

That is exactly the backfill: six orders worth 65.00.

**Defect A: `models/daily_revenue.sql` never picks up the backfill.**

- The model is incremental. On each run it only rebuilds `order_date >= max(order_date) - 3 days`, and `max(order_date)` is in April 2018.
- Late-arriving orders dated Jan/Feb fall outside that window, so they are never inserted.
- Every test passes because they all check `daily_revenue` against itself (unique/not_null). Nothing compares it to `orders`.

**Defect B: the backfilled rows in `raw_orders.ordered_at` are in UTC, while the rest of `raw_orders` is shop-local time.**

- One backfilled order shows up in **January** in `orders`, but the store's sync screen puts all six in February. The first one was placed at 2018-02-01 04:48:19 local time.
- The other backfilled rows fit the same pattern:
  - Order 101 has `order_date` 2018-02-02. The sync screen has an order on **3 Feb** and none on 2 Feb.
  - Orders 102, 103 and 104 have the 8th, 13th and 22nd, which match.
- The size of the shift is constrained by the visible timestamps:
  - Order 102 at 14:00:05 and order 104 at 15:47:33 still land on the right day, so the offset is under about 8h12m.
  - The 04:48:19 order crossed midnight, so the offset is at least about 4h49m.
  - Of the Australian zones, only **UTC+8 (Perth, AWST)** fits. So 2018-02-01 04:48:19 AWST was loaded as 2018-01-31 20:48:19.
- `stg_orders.sql` does `cast(ordered_at as date)` on a naive `TIMESTAMP`. Mixed conventions in that column therefore produce wrong `order_date`s silently.
- Ops matched every day before this load, so local time is the convention that is actually in use. The schema.yml docs saying "Date (UTC)" are also wrong.

## 2. Fix

**Data fix.** Correct the six backfilled rows, ideally by having ingestion re-deliver them in local time.

Find the rows first. They are probably ids 100–105, since 101–104 are visible in the samples:

```sql
select * from raw_orders where id >= 100 order by id;
```

Then confirm the offset: order 1's `ordered_at` should read 2018-01-31 20:48:19. Once confirmed, apply the correction:

```sql
update raw_orders
set ordered_at = ordered_at + interval 8 hour
where id in (/* the six backfilled ids */);
```

Making it in-warehouse only lasts until the next reload, so the ingestion job also needs to convert that store's sync to local time.

**Rebuild the incremental model.** It will not heal itself:

```bash
dbt build --select daily_revenue --full-refresh
```

After that, February should read 32 / 474.00 in both tables, with Jan 30 / 502.00 back to 29 / 496.00 in both.

**Prevent a recurrence.**

- Add a scheduled full refresh of `daily_revenue`, for example weekly. It is a tiny table, so the simplest option is to make it `materialized='table'`.
- Alternatively, have ingestion stamp a `loaded_at` column and drive the incremental filter off that instead of `order_date`.

## 3. Tests that would have caught it

**Reconciliation test** (catches Defect A). Save as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with expected as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)

select
    coalesce(e.order_date, d.order_date) as order_date,
    e.orders as expected_orders, d.orders as actual_orders,
    e.revenue as expected_revenue, d.revenue as actual_revenue
from expected e
full outer join {{ ref('daily_revenue') }} d
    on e.order_date = d.order_date
where e.order_date is null
   or d.order_date is null
   or e.orders != d.orders
   or abs(coalesce(e.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

This would have failed last night on 2018-01-31, 2018-02-02 and the other backfill dates.

**Timezone shift** (Defect B). No schema test can spot a wall-clock shift from the data alone. You need a control total from the source system:

- Have the ingestion job land per-store, per-local-day order counts from the shop admin or sync screen, for example `raw_order_controls(store, order_date, orders)`.
- Add a singular test that `stg_orders` counts by `order_date` equal those controls.
- That test would have flagged 31 Jan (+1), 2 Feb (+1), 1 Feb (−1) and 3 Feb (−1).

`raw_orders` has no store column, so the backfilled rows cannot be told apart in SQL today. Adding a `store_id` (and a `loaded_at`) to the feed is worth requesting for the same reason.
