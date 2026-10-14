claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 56 s | USD 0.193 | 2026-10-14 | project f769fdf | prompt 59260993e394

# Diagnosis: `orders.order_date` is computed in the wrong time zone after mid‑March

## 1. Root cause

**The bad data is in `seeds/raw_orders.csv`, column `ordered_at`. `models/staging/stg_orders.sql` turns it into a date without saying which time zone it is in.**

- `raw_orders.ordered_at` is a plain `TIMESTAMP` with no time zone attached.
- `stg_orders.sql` does `cast(ordered_at as date) as order_date`. So `order_date` is whatever calendar day the raw value happens to be written in.
- Up to about 14 March, the feed wrote `ordered_at` in **IST** (India time). That is why it always agreed with the admin screen. The `schema.yml` description "Date (UTC)" was already wrong at that point.
- From about 15 March, the feed writes `ordered_at` in **UTC**, still with no time zone marker. IST is UTC+5:30. So any order placed between 18:30 and 24:00 UTC falls on the **next** day in India, but the cast keeps it on the UTC day.

The sample you sent shows this exactly. Adding 5:30 to each timestamp gives the IST day:

| order_id | ordered_at (UTC) | IST | our date | admin date |
|---|---|---|---|---|
| 82 | 03-23 23:44 | 03-24 05:14 | 03-23 | 03-24 |
| 87 | 03-26 21:12 | 03-27 02:42 | 03-26 | 03-27 |
| 88 | 03-26 21:51 | 03-27 03:21 | 03-26 | 03-27 |
| 89 | 03-27 18:45 | 03-28 00:15 | 03-27 | 03-28 |

Applying those shifts turns our counts (23:5, 24:1, 26:5, 27:1, 28:0) into the admin counts (23:4, 24:2, 26:3, 27:2, 28:1).

The other mismatches follow the same pattern:
- 03-15 → 03-16
- 04-01 → 04-02
- 04-02 → 04-03

The order ids run in time order: 1–99, with ids 78–89 matching the sample above. That makes the shifted orders 73, 92 and 93. Their timestamps aren't in what you sent, so check that they fall at or after 18:30 UTC.

**Why monthly totals and amounts still match:** an order only moves to the next day. Here none of the moves crosses a month boundary: 31 March stays put, and 1 April moves to 2 April. Payments are joined by `order_id`, so the amounts don't change.

**Why every test passes:** none of the tests check what the dates mean. They only check uniqueness, nulls and accepted values.

## 2. Fix

**Best fix (data):** have the feed or loader write `ordered_at` consistently, with a time zone attached. Then reload `raw_orders`, back‑filling the pre‑March rows as UTC. After that, staging only needs to convert to IST.

**Fix in code, until then:** treat rows from the cutover onward as UTC and the older rows as IST, and derive the date in IST. From the counts, the first UTC row is `order_id` 73. Confirm this against the date the loader was changed.

In `models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,

        -- the feed sent IST wall-clock times until order 73, UTC from then on
        case
            when id < {{ var('orders_utc_from_id', 73) }}
                then (ordered_at at time zone 'Asia/Kolkata') at time zone 'UTC'
            else ordered_at
        end as ordered_at_utc,

        cast(
            case
                when id < {{ var('orders_utc_from_id', 73) }} then ordered_at
                else (ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata'
            end
        as date) as order_date,

        status

    from source

)
```

How the conversion works:
- `ts at time zone 'X'` on a naive timestamp reads it as time zone X and returns a `TIMESTAMPTZ`.
- Applying `at time zone 'Asia/Kolkata'` to that returns IST wall‑clock time.
- This needs DuckDB's ICU extension, which is bundled in standard builds. India has no daylight saving, so `+ interval 330 minute` would work just as well.

In `models/schema.yml`, change the `order_date` description on both `orders` and `customers` to "Date (IST, Asia/Kolkata)". Separately, the amounts are documented as AUD for a shop in India, which is probably also wrong.

**Rebuild `daily_revenue` from scratch.** It is incremental and only rebuilds the last three days. Without a full refresh it keeps the wrong rows for 15 March and 23–28 March:

```
dbt build --full-refresh -s stg_orders+
```

## 3. A test that would have caught it

A reconciliation test against the admin screen. This is the check ops do by hand every morning. Add the admin daily export as a seed, `admin_daily_orders(order_day date, orders int)`, then add `tests/assert_daily_orders_match_admin.sql`:

```sql
with ours as (
    select order_date as day, count(*) as orders
    from {{ ref('orders') }}
    group by 1
),

admin as (
    select order_day as day, orders
    from {{ ref('admin_daily_orders') }}
)

select
    coalesce(ours.day, admin.day) as day,
    ours.orders as dbt_orders,
    admin.orders as admin_orders
from ours
full outer join admin on ours.day = admin.day
where ours.orders is distinct from admin.orders
```

This test would have failed on the first build after the feed change, flagging 15/16 March.

Also add a dbt unit test on `stg_orders` to lock in the conversion. Given input `id: 82, ordered_at: '2018-03-23 23:44:48'`, it should expect `order_date: '2018-03-24'`.
