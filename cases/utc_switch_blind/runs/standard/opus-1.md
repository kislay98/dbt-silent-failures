claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 45 s | USD 0.224 | 2026-10-15 | project ad0b16b | prompt 9fc7feb7aef0

## 1. Root cause

**The bad data is in `raw_orders.ordered_at`. It is read without any timezone handling in `models/staging/stg_orders.sql`.**

```sql
cast(ordered_at as date) as order_date
```

`ordered_at` is a naive `TIMESTAMP` with no zone attached. Until mid-March the app wrote it in IST, so this cast gave the IST day, and that matched the admin screen. From some point in mid-March, the app started writing **UTC** into the same column. For those rows, the cast gives the UTC day.

IST is UTC+5:30, so any order placed between 00:00 and 05:30 IST is stored as 18:30–23:59 UTC on the previous day. Those orders land on the wrong `order_date`. Your sample shows this directly:

| order | `ordered_at` (stored) | `order_date` in dbt | IST time | Admin screen |
|---|---|---|---|---|
| 73 | 2018-03-15 19:45:20 | 03-15 | 03-16 01:15 | 03-16 has 1, no 03-15 row |
| 88 | 2018-03-26 21:51:20 | 03-26 | 03-27 03:21 | 03-27 |
| 92 | 2018-04-01 20:55:29 | **04-01** | 04-02 02:25 | 04-02 has 1, **no 04-01 row** |

Rows from before the switch are clearly local time. For example, order 28 at 2018-01-29 22:50 and order 50 at 02-23 18:53 never disagreed with the admin screen.

This explains the symptoms:
- **Monthly totals and `sum(amount)` still match.** Orders only move by one day, and only across a month boundary if they fall on the last evening of a month.
- **Every test passes.** No test looks at which day an order falls on. The column docs in `models/schema.yml` also say "Date (UTC)", which hides the mismatch: ops actually need the IST business day.

`daily_revenue.sql` has no bug of its own. It just inherits the wrong `order_date`. However, because it is incremental and only rebuilds the last 3 days, it will **keep the wrong history even after you fix staging** unless you do a full refresh.

## 2. Fix

The rows are already mixed, so staging has to normalise them using a cutover point. Order ids increase over time, so use an id as the cutover rather than a timestamp. A timestamp cutover would be ambiguous because the IST and UTC values overlap by 5.5 hours.

`models/staging/stg_orders.sql`:

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_orders') }}

),

localised as (

    select
        *,
        -- the app wrote IST until mid-March 2018, then switched to UTC from this order onwards
        case
            when id >= {{ var('orders_utc_cutover_id') }}
                then ordered_at + interval '5 hours 30 minutes'
            else ordered_at
        end as ordered_at_ist

    from source

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at_ist as ordered_at,
        cast(ordered_at_ist as date) as order_date,
        status

    from localised

)

select * from renamed
```

Then make three more changes:

1. **Set the cutover** in `dbt_project.yml` under `vars: orders_utc_cutover_id: <id>`. The admin data narrows it down: order 73 is already in UTC. Confirm the exact id from the app's deploy date, or by finding the first evening order where the admin screen disagrees with its stored date:
   ```sql
   select id, ordered_at from raw_orders
   where ordered_at >= '2018-03-01' and cast(ordered_at as time) >= '18:30'
   order by id;
   ```
2. **Rebuild the incremental model's history:** `dbt build --full-refresh -s daily_revenue+`. Without this, `daily_revenue` keeps the old dates for everything older than 3 days.
3. **Update the docs:** change `order_date`, `first_order` and `most_recent_order` in `models/schema.yml` from "Date (UTC)" to "Date (IST)".

For the longer term, ask the app team to send `ordered_at` as a `timestamptz` or with an explicit offset. Then staging can use `ordered_at at time zone 'Asia/Kolkata'` and you can drop the cutover var.

## 3. A test that would have caught it

Reconcile against the system ops trust. Load the admin screen's daily export as a seed (`seeds/admin_daily_orders.csv` with columns `order_date,orders`, refreshed by the same feed as the other raw tables). Then add a singular test, `tests/assert_daily_orders_match_admin.sql`:

```sql
-- daily order counts must match the admin screen, which reports in IST
with dbt_counts as (

    select order_date, count(*) as orders
    from {{ ref('orders') }}
    group by order_date

),

admin as (

    select order_date, orders from {{ ref('admin_daily_orders') }}

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

On the current data this test fails, for example on 2018-04-01 (dbt 1, admin missing) and 2018-04-02 (dbt missing, admin 1).

Add a second test pointed at `ref('daily_revenue')` instead of `orders`. That version also catches the incremental model drifting from its source, which is exactly what would happen after the staging fix if nobody ran `--full-refresh`.
