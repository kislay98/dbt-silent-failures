claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 31 s | USD 0.102 | 2026-10-15 | project ad0b16b | prompt ef736a9770e3

## 1. Root cause

**`models/staging/stg_orders.sql`** derives the business date in UTC:

```sql
cast(ordered_at as date) as order_date
```

`raw_orders.ordered_at` is a naive `TIMESTAMP` holding UTC, and the shop runs on IST (UTC+5:30). Any order placed between 18:30 and 24:00 UTC is already the next calendar day in India, but the warehouse files it under the previous day.

Order 73 shows this exactly:
- Raw value: `2018-03-15 19:45:20`.
- In IST that is `2018-03-16 01:15:20`, which is what the admin screen shows.
- The warehouse has it on 15 March.

`orders`, `customers.first_order` and `most_recent_order` all inherit the UTC date. The `schema.yml` descriptions even say "Date (UTC)", so the UTC date was never decided as a business rule.

The data flags only touch orders in the 18:30–24:00 UTC band, so they appear on scattered days. Monthly totals agree because an order only changes month if it falls in that band on the last day of a month.

**Why `daily_revenue` disagrees with `orders`:** it is incremental with `delete+insert`, and it only rebuilds the last 3 days. Its January to March history matches the admin screen, so those rows were most likely written back when `order_date` was still local. They were never refreshed after the logic changed to UTC. I can't see the model's history, so this part is inferred. `orders` is rebuilt as a full table every run and so shows the UTC dates.

All 35 checks pass because none of them test the meaning of a date. They only check nulls, uniqueness, accepted values and relationships.

## 2. Fix

In `stg_orders.sql`, keep `ordered_at` in UTC and make `order_date` the local day. India has no DST, so a fixed offset is safe and needs no ICU extension:

```sql
        ordered_at,
        -- ordered_at is UTC; the shop reports by India local day (IST, UTC+5:30, no DST)
        cast(ordered_at + interval 330 minute as date) as order_date,
```

Then update the "Date (UTC)" descriptions in `models/schema.yml` for `orders.order_date`, `customers.first_order` and `customers.most_recent_order` to say "Date (IST)".

Rebuild so the stale incremental history is replaced:

```
dbt build --full-refresh
```

The full refresh matters because the incremental model would otherwise keep whatever it holds for anything older than 3 days.

## 3. Tests

**a) Unit test** in `models/staging/schema.yml`, pinning the boundary with order 73 and a row just before it:

```yaml
unit_tests:
  - name: stg_orders_order_date_is_ist
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}
          - {id: 74, user_id: 20, ordered_at: '2018-03-15 18:29:59', status: completed}
          - {id: 75, user_id: 21, ordered_at: '2018-03-15 18:30:00', status: completed}
    expect:
      rows:
        - {order_id: 73, order_date: '2018-03-16'}
        - {order_id: 74, order_date: '2018-03-15'}
        - {order_id: 75, order_date: '2018-03-16'}
```

**b) Reconciliation test** `tests/assert_daily_revenue_matches_orders.sql`. This catches stale incremental history. It returns rows on any mismatch, so it fails:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)
select coalesce(o.order_date, d.order_date) as order_date
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.order_date is null
   or d.order_date is null
   or o.orders != d.orders
   or abs(o.revenue - d.revenue) > 0.005
```

Before the fix, test (a) fails. Test (b) would have failed against the UTC-dated `orders` and flagged the disagreement.

Unrelated to this bug, but worth a look: the regions are Australian states and the amounts are labelled AUD, though the shop is in India.
