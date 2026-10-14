claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 51 s | USD 0.202 | 2026-10-15 | project ad0b16b | prompt 11fb6c29d933

## 1. Root cause

**The ingestion job renamed the order timestamp column, and `models/staging/stg_orders.sql` still reads the old one.**

`raw_orders` now has two timestamp columns: the original `ordered_at` and a new `order_ts`.

- For January and February orders, both columns are filled and hold identical values (e.g. order 25: `2018-01-28 09:00:52` in both).
- For every order from March onward, `ordered_at` is NULL and the timestamp exists only in `order_ts` (e.g. order 63: `ordered_at` NULL, `order_ts` `2018-03-06 12:28:52`).

`stg_orders.sql` computes `cast(ordered_at as date) as order_date`. That makes `order_date` NULL for the 43 March and April orders (29 + 27 + 43 = 99). Their amounts are intact, which is why `sum(amount)` is still 1672.00: the 761.00 in the NULL bucket is exactly March 622 + April 139.

**Why nothing failed:**

- No test checks `order_date` for nulls. The docs block for `orders.order_date` has no tests, and `stg_orders` only tests `order_id` and `status`.
- **`daily_revenue` only looks correct because it is stale.** It is incremental, and its filter is `order_date >= max(order_date) - 3 days`. NULL dates never match that filter, so the run inserted nothing, deleted nothing, and kept the March and April rows from earlier loads. Its `unique` and `not_null` tests pass on that stale data.
- The same NULLs also flow into `customers`. For example, customer 9 has 1 order but a NULL `first_order`.

**Timezone check needed before fixing.** Ops says `ordered_at` matched the admin screen's local-time dates every day. So `ordered_at` is effectively shop-local time, even though the docs call it UTC. We still need to confirm that `order_ts` uses the same convention for the new rows. Order 73 is the test case. Its admin-screen time is 01:15:20 local, and Australian local time in March (AEDT) is UTC+11, so a UTC value would land on the previous day:

```sql
select id, ordered_at, order_ts from raw_orders where id = 73;
```

- If it returns `2018-03-16 01:15:20`, `order_ts` is local time, like `ordered_at`.
- If it returns `2018-03-15 14:15:20`, the new feed is in UTC and must be converted. A plain cast would put order 73, and every other early-morning order, on the wrong day.

## 2. Fix

In `models/staging/stg_orders.sql`:

**If `order_ts` is local time** (consistent with the overlapping Jan/Feb rows):

```sql
    select
        id as order_id,
        user_id as customer_id,
        coalesce(order_ts, ordered_at) as ordered_at,
        cast(coalesce(order_ts, ordered_at) as date) as order_date,
        status
```

**If `order_ts` is UTC for the new rows**, convert it to the shop's timezone before taking the date. Shown here with `Australia/Sydney`; substitute the shop's actual zone:

```sql
        coalesce(ordered_at, timezone('Australia/Sydney', timezone('UTC', order_ts))) as ordered_at,
        cast(coalesce(ordered_at, timezone('Australia/Sydney', timezone('UTC', order_ts))) as date) as order_date,
```

Then:

- Correct the `order_date` description in `schema.yml` from "Date (UTC)" to the shop's local date, since that is what ops reconciles against.
- Rebuild `daily_revenue` from scratch so it is recomputed from `orders` rather than relying on stale rows:
  ```
  dbt build --full-refresh -s stg_orders+
  ```
- Afterwards, check that the month report shows Jan 29 / Feb 27 / Mar 35 / Apr 8 with no NULL bucket, and that order 73 has `order_date = 2018-03-16`.

## 3. Tests that would have caught it

In `models/staging/schema.yml` (and the same test on `orders.order_date` in `models/schema.yml`):

```yaml
  - name: stg_orders
    columns:
      - name: order_date
        tests:
          - not_null
```

A reconciliation test would also catch the stale incremental hiding the problem. Add `tests/daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must account for every order and every dollar in orders
select *
from (select count(*) as orders, sum(amount) as revenue from {{ ref('orders') }}) o
cross join (select sum(orders) as orders, sum(revenue) as revenue from {{ ref('daily_revenue') }}) d
where o.orders != d.orders or abs(o.revenue - d.revenue) > 0.01
```

The `not_null` test would have failed with 43 rows. The reconciliation test catches any future case where `orders` and `daily_revenue` drift apart, for example when an incremental model keeps rows the source can no longer produce.
