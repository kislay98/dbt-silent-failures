claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 56 s | USD 0.192 | 2026-10-14 | project f769fdf | prompt 59260993e394

## 1. Root cause

**The source data changed timezone partway through March, and `stg_orders.sql` treats every timestamp as local time.**

- `seeds/raw_orders.csv` (`raw_orders.ordered_at`) holds naive `TIMESTAMP`s with no offset. Up to mid-March they were written in **IST**, so `cast(ordered_at as date)` in `models/staging/stg_orders.sql` produced the IST day, and ops' reconciliation matched.
- From around **2018-03-15**, the upstream app started writing `ordered_at` in **UTC**. The column is still naive, so nothing failed. But any order placed between 00:00 and 05:30 IST now lands on the previous day.

The rows you sampled fit an exact +5:30 shift:

| order_id | ordered_at (UTC) | IST | our date | admin date |
|---|---|---|---|---|
| 82 | 03-23 23:44:48 | 03-24 05:14 | 03-23 | 03-24 |
| 87 | 03-26 21:12:29 | 03-27 02:42 | 03-26 | 03-27 |
| 88 | 03-26 21:51:20 | 03-27 03:21 | 03-26 | 03-27 |
| 89 | 03-27 18:45:59 | 03-28 00:15 | 03-27 | 03-28 |

Order 89 settles it. It is only 15 minutes past the 18:30 UTC boundary, and admin puts it on the 28th. Applying +5:30 to these rows reproduces the admin counts exactly: 23rd=4, 24th=2, 26th=3, 27th=2, 28th=1. The same pattern explains 15th→16th and 1 Apr→2 Apr→3 Apr.

Monthly totals and `sum(amount)` still match because each order moves by at most one day and none crossed a month boundary. No test fires because every row is still unique, not null and valid. Only its date is wrong.

Two things make this worse:
- `daily_revenue` is incremental with a 3-day lookback, so it has **already persisted the wrong dates**. Fixing `orders` alone won't correct it.
- `schema.yml` documents `order_date` as "Date (UTC)". That was never true before March (the data was IST), and it hid the problem.

## 2. Fix

**Preferred: fix the data at source.** Have upstream emit `ordered_at` with an explicit offset (ISO 8601 `+00:00`) and load it as `TIMESTAMPTZ`. Normalize the historical IST rows to UTC once, then derive the business date in IST in staging.

**Code fix in dbt**, if the raw data can't be rewritten. First find the first order written in UTC; order ids are monotonic, so that is a cutover id. The 03-15 order is the first one *visibly* shifted, but earlier rows may already be UTC. Confirm by checking the `raw_orders` rows from roughly 03-10 to 03-15 against the admin screen per order, or by asking for the app's deploy date.

`models/staging/stg_orders.sql`:

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_orders') }}

),

normalized as (

    select
        *,
        -- the app wrote ordered_at in IST up to the cutover and in UTC after it;
        -- IST has no DST, so a fixed +5:30 offset is exact
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

    from normalized

)

select * from renamed
```

In `dbt_project.yml`:

```yaml
vars:
  orders_utc_cutover_id: <first UTC-written order id>
```

Then:
- Rebuild the incremental model so past days are rewritten: `dbt build --full-refresh -s stg_orders+`
- Change the `order_date` / `first_order` / `most_recent_order` descriptions from "Date (UTC)" to "Date (IST)". While you're there, the amounts are documented as "AUD", which looks wrong for an Indian shop.

## 3. Tests that would have caught it

**a) Reconciliation against the admin screen.** This is the test that would actually have fired in March. Load the admin export as a seed or source, e.g. `admin_daily_orders` with columns `day, orders`, and add `tests/assert_daily_orders_match_admin.sql`:

```sql
with ours as (

    select order_date as day, count(*) as orders
    from {{ ref('orders') }}
    group by 1

),

admin as (

    select day, orders from {{ ref('admin_daily_orders') }}

)

select
    coalesce(ours.day, admin.day) as day,
    ours.orders as our_orders,
    admin.orders as admin_orders

from ours
full outer join admin on ours.day = admin.day

where ours.orders is distinct from admin.orders
```

This automates what ops does by hand every morning. It would have failed on 2018-03-15/16.

**b) Unit test pinning the conversion,** so the fix can't regress (dbt ≥ 1.8):

```yaml
unit_tests:
  - name: stg_orders_order_date_is_ist
    model: stg_orders
    overrides:
      vars:
        orders_utc_cutover_id: 70
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 1,  user_id: 1, ordered_at: '2018-01-01 23:30:00', status: completed}  # pre-cutover, already IST
          - {id: 82, user_id: 1, ordered_at: '2018-03-23 23:44:48', status: completed}  # UTC -> 03-24 05:14 IST
          - {id: 89, user_id: 1, ordered_at: '2018-03-27 18:45:59', status: completed}  # UTC -> 03-28 00:15 IST
    expect:
      rows:
        - {order_id: 1,  order_date: '2018-01-01'}
        - {order_id: 82, order_date: '2018-03-24'}
        - {order_id: 89, order_date: '2018-03-28'}
```

The long-term guard is a model contract declaring `ordered_at` as `timestamptz`. That makes "which timezone is this?" impossible to leave ambiguous again.

I couldn't run any of this because only the listings were provided, not the project files. The +5:30 shift is checked by hand against every row you sampled. The exact cutover id still needs confirming from `raw_orders`.
