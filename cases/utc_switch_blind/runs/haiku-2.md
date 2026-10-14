claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 46 s | USD 0.007 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`models/staging/stg_orders.sql`**, in the line `cast(ordered_at as date) as order_date`. The date is taken from `ordered_at` without any timezone conversion, and `ordered_at` is now stored in UTC.

The admin screen shows India Standard Time (UTC+5:30). Every mismatch in your table fits exactly one rule: an order placed at or after 18:30 UTC is on the next calendar day in IST.

| order_id | ordered_at (UTC) | Warehouse day | Admin day |
|---|---|---|---|
| 82 | 2018-03-23 23:44:48 | 03-23 | 03-24 |
| 87 | 2018-03-26 21:12:29 | 03-26 | 03-27 |
| 88 | 2018-03-26 21:51:20 | 03-26 | 03-27 |
| 89 | 2018-03-27 18:45:59 | 03-27 | 03-28 |
| 81 | 2018-03-23 15:53:11 | 03-23 | 03-23 (unchanged) |
| 84–86 | 07:04, 11:50, 15:45 UTC | 03-26 | 03-26 (unchanged) |

The sample is consistent with this across all five moved or unmoved orders, and the day-by-day counts also line up. For example, admin 03-23 = 4 is warehouse 5 minus order 82, and admin 03-24 = 2 is warehouse 1 plus order 82.

**Why it started in mid-March:** the two views agree through 14 March and first disagree on 15 March (warehouse has an order on 03-15 that admin puts on 03-16). The likely explanation is that the `ordered_at` feed changed from the app's local wall-clock time to UTC around 15 March. Before the change, the stored value and the local date matched. After it, the date is computed from UTC. The `orders` and `stg_orders` schema docs already say "Date (UTC)", so the docs were describing the new behaviour while ops reconciles against local time. I can't confirm the cutover date from the sample alone; see the check below.

## 2. Fix

Convert to IST in `stg_orders.sql` before taking the date, and keep `ordered_at` unchanged:

```sql
cast(timezone('Asia/Kolkata', timezone('UTC', ordered_at)) as date) as order_date,
```

Before shipping, confirm the feed really is UTC and find when it changed. If `ordered_at` were already IST, this would shift every order by another 5h30m. Run:

```sql
select cast(ordered_at as date) as d,
       min(extract(hour from ordered_at)) as min_hour,
       max(extract(hour from ordered_at)) as max_hour,
       count(*) as n
from raw_orders
where ordered_at between '2018-03-01' and '2018-03-31'
group by 1 order by 1;
```

Before 15 March you should see no orders at or after 18:30 UTC (hour 18 with minute ≥ 30, or hour ≥ 19), and after it you should see some. If instead the pre-March data also has late-evening orders that match admin, the cause is somewhere else and this fix is wrong.

Once confirmed, rebuild:

```
dbt build -s stg_orders+ --full-refresh
```

`--full-refresh` is needed because `daily_revenue` is incremental and only rebuilds the last three days, so it would keep the old counts for earlier dates.

Also update the descriptions in `models/schema.yml` (`order_date`, `first_order`, `most_recent_order`) from "Date (UTC)" to "Date (IST)", since the business date is now local.

## 3. Test that would have caught it

The existing tests (`unique`, `not_null`, `accepted_values`) never check that `order_date` has the right value, so a wrong date passes. Add a dbt unit test to `models/staging/schema.yml` that pins the IST boundary:

```yaml
unit_tests:
  - name: order_date_is_ist_calendar_date
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 1, user_id: 1, ordered_at: '2018-03-23 18:29:59', status: completed}
          - {id: 2, user_id: 1, ordered_at: '2018-03-23 18:30:00', status: completed}
          - {id: 3, user_id: 1, ordered_at: '2018-03-23 23:44:48', status: completed}
    expect:
      rows:
        - {order_id: 1, order_date: '2018-03-23'}
        - {order_id: 2, order_date: '2018-03-24'}
        - {order_id: 3, order_date: '2018-03-24'}
```

This test fails on the current code and passes after the fix.

For the check ops actually performs, add a singular test or a seed of the admin's daily counts and compare `orders` against it grouped by `order_date`. That would catch future drift from the app's view, not just the timezone bug.

## Side note

`daily_revenue.sql` has a comment saying "payments can land a day or two after the order". `daily_revenue` is built from `orders`, not payments, so that reason is wrong, and the three-day rebuild window doesn't protect against changes to historical order dates. Fixing the comment and the window logic is a separate change.
