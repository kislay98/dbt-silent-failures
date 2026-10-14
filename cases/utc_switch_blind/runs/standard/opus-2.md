claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 55 s | USD 0.245 | 2026-10-15 | project ad0b16b | prompt 9fc7feb7aef0

I couldn't find the project or seeds on disk, so this analysis uses only the samples you pasted. It identifies the bug clearly, but it can't pin the exact cutover row. That last step needs one query, given below.

## 1. Root cause

**The data:** `seeds/raw_orders.csv` (loaded as `raw_orders.ordered_at`, a `TIMESTAMP` with no time zone) changed what its timestamps mean partway through March 2018:

- **Before the change**, `ordered_at` was **India local time (IST)**.
- **Since around 12–15 March**, `ordered_at` is **UTC**, with nothing marking the change.

**The code:** `models/staging/stg_orders.sql` does `cast(ordered_at as date) as order_date`. This assumes every row is in the same frame. It was correct while the feed was IST. Since the switch, any order placed between 00:00 and 05:30 IST (18:30–24:00 UTC) gets the previous day's date.

Evidence from your samples:

| order_id | `ordered_at` (raw) | `order_date` (dbt) | IST (+5:30) | Admin screen |
|---|---|---|---|---|
| 73 | 2018-03-15 19:45:20 | 03-15 | **03-16** 01:15 | no 03-15 row; 03-16 = 1 |
| 88 | 2018-03-26 21:51:20 | 03-26 | **03-27** 03:21 | — |
| 92 | 2018-04-01 20:55:29 | 04-01 | **04-02** 02:25 | **no 04-01 row at all**; 04-02 = 1 |

Order 92 is the clearest case. dbt has an order on 2018-04-01, and the admin screen has none that day.

Earlier rows can't have been UTC. Order 28 (`2018-01-29 22:50`) and order 50 (`2018-02-23 18:53`) would then have landed on the next IST day, and ops would have seen mismatches before March.

This fits all the other symptoms:
- **Monthly totals still match.** Orders only move by one day, and none crossed a month boundary.
- **`sum(amount)` is unchanged.** No rows are lost or duplicated.
- **Every test passes.** Nothing checks how dates are assigned to days.
- `models/schema.yml` describes `order_date` as "Date (UTC)", but ops use it as an IST business date, so the documentation hides the problem.

## 2. Fix

**Best fix:** have the source system export a time zone, or always export UTC, and backfill the old rows. Until that happens, normalise the data in staging.

First, find the first order exported in UTC. Order IDs increase over time, and an ID is safer to split on than a timestamp, because the two time frames overlap by 5.5 hours. Run this from March on and compare it with the admin screen:

```sql
select order_id, ordered_at, order_date
from stg_orders
where ordered_at >= '2018-03-08'
order by order_id;
```

Pick the first ID whose shifted date matches the admin screen. It is at or before order 73. Ideally, confirm it against the export or deploy change on the app side.

Then update `models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- the orders feed switched from IST to UTC at order id {{ var('orders_utc_from_order_id') }};
        -- normalise everything to IST, which is what the business day is reconciled in
        case
            when id >= {{ var('orders_utc_from_order_id') }}
                then ordered_at + interval '5 hours 30 minutes'
            else ordered_at
        end as ordered_at,
        status

    from source

),

final as (

    select
        *,
        cast(ordered_at as date) as order_date

    from renamed

)

select * from final
```

Add the variable to `dbt_project.yml`, using the ID you found:

```yaml
vars:
  orders_utc_from_order_id: 73   # replace with the confirmed first UTC order id
```

India has no daylight saving time, so a fixed +5:30 offset is exact. You could instead use `(ordered_at at time zone 'UTC') at time zone 'Asia/Kolkata'` if the ICU extension is loaded.

Also change the `order_date`, `first_order` and `most_recent_order` descriptions in `models/schema.yml` from "Date (UTC)" to "Date (IST)".

**Important:** `daily_revenue` is incremental and only rebuilds the last 3 days. Wrongly dated rows such as 2018-04-01 would stay in the table, so rebuild it once:

```
dbt build --full-refresh -s stg_orders+
```

## 3. A test that would have caught this

Ops already do this check every morning, so add it to the project. Export the admin screen's daily counts as a seed, `seeds/admin_daily_orders.csv` with columns `order_date,orders`, and add a singular test:

```sql
-- tests/assert_daily_orders_match_admin.sql
-- order counts per day must match the app's admin screen (IST business days)
with dbt_counts as (

    select order_date, count(*) as orders
    from {{ ref('orders') }}
    group by order_date

),

admin as (

    select order_date, orders
    from {{ ref('admin_daily_orders') }}

)

select
    coalesce(dbt_counts.order_date, admin.order_date) as order_date,
    dbt_counts.orders as dbt_orders,
    admin.orders as admin_orders

from dbt_counts

full outer join admin
    on dbt_counts.order_date = admin.order_date

where dbt_counts.orders is distinct from admin.orders
```

On the current build, this test fails on 03-15/03-16, 03-26/03-27 and 04-01/04-02, among others.

Two cheaper guards are also worth adding:
- Run the same comparison against `daily_revenue`.
- Warn when the share of orders with `hour(ordered_at)` between 18 and 23 changes sharply from week to week. This flags an unannounced time zone change in the feed even without an admin export.
