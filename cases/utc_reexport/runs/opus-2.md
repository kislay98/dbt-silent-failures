claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 54 s | USD 0.192 | 2026-10-14 | project f769fdf | prompt 9aa546ec9ab1

# Diagnosis: `order_date` is the UTC calendar day, but ops counts orders by Indian local day

## 1. Root cause

**File:** `models/staging/stg_orders.sql`

```sql
cast(ordered_at as date) as order_date,
```

`raw_orders.ordered_at` is a plain `TIMESTAMP` with no time zone, and its values are UTC. Casting it to `date` gives the UTC day. The admin screen shows the day in India (IST, UTC+05:30). Any order placed between 00:00 and 05:30 IST (18:30–23:59 UTC) lands on the previous day in the warehouse.

**Order 73 shows this exactly:** `2018-03-15 19:45:20` UTC + 5:30 = `2018-03-16 01:15:20` IST, the time ops saw on the admin screen.

The other sampled orders fit too:

| order | ordered_at (UTC) | IST | warehouse day → admin day |
|---|---|---|---|
| 16 | 01-17 21:07 | 01-18 02:37 | 17th → 18th (17th: 4→3, 18th: 1→2) |
| 82 | 03-23 23:44 | 03-24 05:14 | 23rd → 24th (5→4, 1→2) |
| 87, 88 | 03-26 21:12, 21:51 | 03-27 02:42, 03:21 | 26th → 27th (5→3) |
| 89 | 03-27 18:45 | 03-28 00:15 | 27th → 28th (27th: 1−1+2 = 2, 28th: 0→1) |
| 1, 12, 99 | 11:38, 10:13, 18:05 | same day | unchanged |

The same pattern explains the rest of January: 6th→7th, 10th→11th, 13th→14th. Monthly totals still agree because none of the moved orders crosses a month boundary in this data.

**What changed in the data:** `daily_revenue` is incremental and only rebuilds its last three days. Its January–March rows were built earlier and match the admin screen. So when they were built, the plain `cast(... as date)` produced IST days, meaning `ordered_at` held IST wall-clock time then. The ingestion job has since started loading (and reloaded history as) **UTC** into the same naive `TIMESTAMP` column. The model code didn't change, but its meaning did.

Two things confirm this:
- The recent rows of `daily_revenue` (April) were rebuilt after the switch and now disagree.
- The stale dashboard-only dates (the `NULL` rows in the full outer join) are the IST days that no longer exist in `orders`.

Please confirm the switch date with the ingestion team. This is inferred from the outputs, not from a load log.

No test failed because nothing checks what timezone a timestamp is in. The `order_date` docs ("Date (UTC)") also describe the wrong day for this business.

Downstream impact: `orders.order_date`, `customers.first_order` / `most_recent_order`, and `daily_revenue` (once rebuilt).

## 2. Fix

`models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- raw ordered_at is UTC (naive timestamp); the shop reports by local day in India
        ordered_at as ordered_at_utc,
        cast((ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata' as date) as order_date,
        status

    from source

)
```

Notes:
- DuckDB uses the ICU extension for named zones, and dbt-duckdb normally has it available. If it isn't, `cast(ordered_at + interval '5 hours 30 minutes' as date)` is equivalent, since IST has no DST.
- If anything selects `ordered_at` from `stg_orders`, keep that name instead of renaming to `ordered_at_utc`.
- Update the docs in `models/schema.yml`: `order_date`, `first_order` and `most_recent_order` should say "Date (IST, Asia/Kolkata)" instead of "Date (UTC)".

Then rebuild `daily_revenue` fully. A normal run only replaces the last three days, so the wrong April rows would stay:

```bash
dbt build --full-refresh -s stg_orders+
```

## 3. Tests that would have caught it

**a) Unit test pinning the local-day rule** (add to `models/staging/schema.yml`; dbt ≥ 1.8). It includes the 18:30 UTC midnight boundary:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_india_local_day
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}  # 01:15 IST on the 16th
          - {id: 12, user_id: 59, ordered_at: '2018-01-15 10:13:37', status: completed}  # same day
          - {id: 900, user_id: 1, ordered_at: '2018-03-15 18:29:59', status: placed}     # 23:59:59 IST
          - {id: 901, user_id: 1, ordered_at: '2018-03-15 18:30:00', status: placed}     # 00:00:00 IST next day
    expect:
      rows:
        - {order_id: 73,  order_date: '2018-03-16'}
        - {order_id: 12,  order_date: '2018-01-15'}
        - {order_id: 900, order_date: '2018-03-15'}
        - {order_id: 901, order_date: '2018-03-16'}
```

**b) Reconciliation test** between the incremental dashboard and its source. This is what would have flagged the ingestion change on the first night. Add as `tests/assert_daily_revenue_matches_orders.sql`:

```sql
with orders_by_day as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as warehouse_orders,
    d.orders as dashboard_orders,
    o.revenue as warehouse_revenue,
    d.revenue as dashboard_revenue
from orders_by_day o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or round(o.revenue, 2) is distinct from round(d.revenue, 2)
```

The unit test locks the conversion logic. The reconciliation test catches the case here, where the code is unchanged but the meaning of the raw column shifts, or the incremental history drifts from a full rebuild.
