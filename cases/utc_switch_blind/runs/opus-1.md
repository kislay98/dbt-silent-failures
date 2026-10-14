claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 61 s | USD 0.201 | 2026-10-14 | project f769fdf | prompt 59260993e394

## 1. Root cause

The bug is in the data, not the SQL. Around mid-March, the source system changed what `raw_orders.ordered_at` means. Before that, it wrote naive **IST** wall-clock times. After that, it writes naive **UTC** times. The column is a plain `TIMESTAMP` with no offset, so nothing failed.

`models/staging/stg_orders.sql` does `cast(ordered_at as date) as order_date`. That gives the correct IST day for the old rows but the UTC day for the new ones. Any order placed between 18:30 and 23:59 UTC (00:00–05:29 IST the next day) is counted one day early.

Every disagreement in your table fits a +5:30 shift:

| Order (UTC `ordered_at`) | IST | dbt day → admin day |
|---|---|---|
| (one order, ≥18:30 UTC) | next day | 03-15 → 03-16 |
| 82 `03-23 23:44:48` | 03-24 05:14 | 03-23 → 03-24 |
| 87 `03-26 21:12:29` | 03-27 02:42 | 03-26 → 03-27 |
| 88 `03-26 21:51:20` | 03-27 03:21 | 03-26 → 03-27 |
| 89 `03-27 18:45:59` | 03-28 00:15 | 03-27 → 03-28 |
| (two orders, ≥18:30 UTC) | next day | 04-01 → 04-02, 04-02 → 04-03 |

Orders before 18:30 UTC, such as 81 at 15:53 and 83 at 01:35, stay on the same day, which is why they match. No order crosses a month boundary, so monthly counts and `sum(amount) = 1672` still agree. Nothing is lost, it is only moved.

All 40 tests pass because none of them check dates. They only check keys, nulls and accepted values.

Two related problems:
- `models/schema.yml` describes `order_date` as "Date (UTC)". That is wrong for the business, which needs IST days, and it is only partly true of the data. The "(AUD)" amount descriptions are also wrong for an Indian shop.
- `daily_revenue` is incremental and only rebuilds the last 3 days. Wrong rows from mid-March are already stored there and won't fix themselves.

**Check this before applying the fix.** I don't have the seed CSV, so confirm that the older rows really are IST. This query should show pre-cutover hours spread across the IST day, and post-cutover hours shifted about 5.5 hours earlier:
```sql
select id, ordered_at, hour(ordered_at) from raw_orders order by id;
```
If every row turns out to be UTC (and Jan–Feb matched only because no orders fell after 18:30 UTC), the fix is simpler: convert every row, with no cutover.

## 2. Fix

**Best fix:** have the upstream feed send offset-aware timestamps (`2018-03-23T23:44:48Z`) and load them as `TIMESTAMPTZ`.

**In the project:** store UTC in staging and compute the business date in IST. India has no DST, so a fixed +5:30 offset is safe. Set the cutover to the first order ID written in UTC, which falls between the last order on 03-14 and the order dbt puts on 03-15. Upstream or the raw file history can confirm the exact ID.

```yaml
# dbt_project.yml
vars:
  raw_orders_utc_from_id: 0   # TODO: first order id written in UTC by the source
```

```sql
-- models/staging/stg_orders.sql
with source as (

    select * from {{ ref('raw_orders') }}

),

normalised as (

    select
        *,
        -- the source wrote IST wall-clock times until it switched to UTC in March 2018
        case
            when id < {{ var('raw_orders_utc_from_id') }} then ordered_at - interval 330 minute
            else ordered_at
        end as ordered_at_utc

    from source

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at_utc as ordered_at,
        -- business day is in IST (UTC+05:30, no DST)
        cast(ordered_at_utc + interval 330 minute as date) as order_date,
        status

    from normalised

)

select * from renamed
```

Change the `order_date` description in `schema.yml` to "Date (IST) …". Then rebuild everything, including the incremental model's history:

```bash
dbt build --full-refresh
```

## 3. Tests that would have caught it

**Reconciliation against the admin screen.** This is the test that matters, because the bug was a silent change in meaning that no schema test can see. Load the admin daily counts as a seed or source, e.g. `admin_daily_orders(day, orders)`, and add a singular test:

```sql
-- tests/orders_reconcile_with_admin_daily_counts.sql
with dbt_counts as (
    select order_date as day, count(*) as orders from {{ ref('orders') }} group by 1
),
admin as (
    select day, orders from {{ ref('admin_daily_orders') }}
)
select coalesce(a.day, d.day) as day, a.orders as admin_orders, d.orders as dbt_orders
from admin a
full outer join dbt_counts d on a.day = d.day
where a.orders is distinct from d.orders
```

This would have failed on the first night order 15-March was misdated.

**A unit test that pins the timezone logic** (dbt ≥ 1.8):

```yaml
# models/staging/schema.yml
unit_tests:
  - name: stg_orders_order_date_is_ist
    model: stg_orders
    overrides:
      vars:
        raw_orders_utc_from_id: 100
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 99,  user_id: 1, ordered_at: '2018-03-10 23:30:00', status: placed}  # legacy IST
          - {id: 100, user_id: 1, ordered_at: '2018-03-23 18:29:59', status: placed}  # UTC, same IST day
          - {id: 101, user_id: 1, ordered_at: '2018-03-23 18:30:00', status: placed}  # UTC, next IST day
    expect:
      rows:
        - {order_id: 99,  order_date: '2018-03-10'}
        - {order_id: 100, order_date: '2018-03-23'}
        - {order_id: 101, order_date: '2018-03-24'}
```

The unit test protects the conversion code. The reconciliation test protects against the next silent upstream change.
