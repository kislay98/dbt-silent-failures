claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 34 s | USD 0.082 | 2026-10-14 | project bef6b69 | prompt f253c60e27c9

## 1. Root cause

**`models/staging/stg_orders.sql`** derives the order day with `cast(ordered_at as date)`.

- `raw_orders.ordered_at` is a naive `TIMESTAMP` holding UTC. The sample shows order 73 as `2018-03-15 19:45:20`, which is 16 March 01:15 IST (UTC+5:30), exactly what the admin screen shows.
- The cast takes the UTC calendar day, so any order placed after 18:30 UTC (00:00 IST) lands on the previous local day. The affected orders in your samples are 16 (21:07 UTC), 73 (19:45), 82 (23:44), 87 (21:12) and 88 (21:51).
- This explains why the shifts are one-way and the totals still agree. Orders keep their month except around month boundaries, and the tests (unique, not_null, accepted_values) can't see a wrong-but-valid date.
- `models/schema.yml` documents `orders.order_date`, `first_order` and `most_recent_order` as "Date (UTC)", so the bug was written down as intended.

**Why `daily_revenue` disagrees with `orders`:** `models/daily_revenue.sql` is incremental and only reprocesses `max(order_date) - 3 days`. Its older rows still carry the local-day buckets and were never rebuilt with the UTC logic, which is why it matches the admin screen but not `orders`. I can't see the build history, so this part is inferred from the pattern. The mismatches run from January to early April, and the most recent days (rebuilt each run) are not among them.

## 2. Fix

`stg_orders.sql`:

```sql
select
    id as order_id,
    user_id as customer_id,
    ordered_at,
    -- ordered_at is naive UTC; the shop reports by India local day
    cast(timezone('Asia/Kolkata', timezone('UTC', ordered_at)) as date) as order_date,
    status
from source
```

`timezone('UTC', ordered_at)` marks the naive value as UTC, and `timezone('Asia/Kolkata', ...)` converts it to local time. India has no DST, so `ordered_at + interval 330 minute` would also work and avoids needing ICU.

Then:
1. Update the three "Date (UTC)" descriptions in `schema.yml` to say IST.
2. Rebuild the incremental table, since the normal run won't touch old rows: `dbt build --full-refresh -s stg_orders+ ` (or at least `-s daily_revenue --full-refresh` after rebuilding `orders`). I haven't run this.
3. Re-run ops' comparison for 5–18 Jan and 20–31 Mar. The `orders` and `daily_revenue` counts should now match the admin screen.

If the dashboard's history was built under different logic than the admin screen, that comparison will show it.

## 3. Tests that would have caught it

**Unit test** (in `models/staging/schema.yml`), pinning the day boundary:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_ist
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}
          - {id: 12, user_id: 59, ordered_at: '2018-01-15 10:13:37', status: completed}
          - {id: 1001, user_id: 1, ordered_at: '2018-01-10 18:29:59', status: placed}
          - {id: 1002, user_id: 1, ordered_at: '2018-01-10 18:30:00', status: placed}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
        - {order_id: 12, order_date: '2018-01-15'}
        - {order_id: 1001, order_date: '2018-01-10'}
        - {order_id: 1002, order_date: '2018-01-11'}
```

**Reconciliation singular test** (`tests/assert_daily_revenue_matches_orders.sql`), catching stale incremental history. It returns rows on any mismatch, so the test fails:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
)
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as dash_orders
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or o.revenue is distinct from d.revenue
```

This test would have failed in the nightly build with the 18 date mismatches shown in your comparison output. The unit test would have failed the moment the cast was introduced.
