claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 60 s | USD 0.008 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

`raw_orders.ordered_at` holds **UTC** for recent orders, but ops and the admin screen use **shop local time, UTC+05:30**. `stg_orders.sql` takes `cast(ordered_at as date)` as `order_date`, so any order placed after 18:30 UTC lands on the next local day.

The SQL does what it says. The problem is that the input's timezone changed around 15 March and nothing in the data or tests records it. The `schema.yml` description ("Date (UTC)") also conflicts with what ops expects.

**Evidence** (from the pasted outputs; I did not run anything):

- Order 73 is stored as 2018-03-15 19:45:20, and the admin shows 2018-03-16 01:15:20. That is exactly +05:30.
- Applying +05:30 to the stored `ordered_at` for orders 78–89 reproduces the admin counts:

| Day | Warehouse (UTC date) | Shifted to +05:30 | Admin |
|---|---|---|---|
| 26 Mar | 5 (84–88) | 3 (84, 85, 86) | 3 |
| 27 Mar | 1 (89) | 2 (87, 88) | 2 |
| 28 Mar | 0 | 1 (89) | 1 |

- The same rule predicts 23 Mar: 5 becomes 4 (78–81), and 24 Mar: 1 becomes 2 (82, 83). Check these against the admin screen to confirm.
- Monthly totals match because the shift only moves orders between adjacent days. Only orders near a month boundary change month.
- Why it "always agreed" before mid-March: the two sides only agree if the stored times were local then. So the feed for `raw_orders` most likely switched from local to UTC around 15 March. I can't see the load history from here. Confirm with the source owner or the loader's history.

**Files:**
- `seeds/raw_orders.csv` (the `raw_orders` seed). The timestamps change from local to UTC partway through. This is the data problem.
- `models/staging/stg_orders.sql`. It assumes `ordered_at` is already in the reporting zone, and nothing guards that assumption.

## 2. Fix

**Step 1: find the cutover.** Compare admin times with `ordered_at` for orders around 10–16 March to find the first order stored in UTC. Use that instant in place of the placeholder below. I can't determine it from the pasted data.

**Step 2: normalize in staging.** Replace `models/staging/stg_orders.sql` with:

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_orders') }}

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,

        -- Orders from the cutover onward are stored in UTC; earlier rows are already shop-local.
        -- TODO: replace the placeholder with the exact UTC cutover instant.
        case
            when ordered_at >= timestamp '2018-03-15 00:00:00'
                then ordered_at + interval '5 hours 30 minutes'
            else ordered_at
        end as ordered_at_local,

        status

    from source

),

final as (

    select
        order_id,
        customer_id,
        ordered_at,
        ordered_at_local,
        cast(ordered_at_local as date) as order_date,
        status

    from renamed

)

select * from final
```

I used a fixed +05:30 offset because it doesn't change with DST. If the shop's zone observes DST, use a real timezone conversion instead. Once the source is confirmed to be consistent, the `case` can be removed.

**Step 3: rebuild with full refresh.**

```
dbt build --full-refresh -s stg_orders+
```

The full refresh is required. `daily_revenue` only rebuilds the last three days, so wrong days older than that would otherwise remain.

**Step 4: fix the docs.** In `models/schema.yml`, change `order_date` on `orders` and `customers` (`first_order`, `most_recent_order`) and the `daily_revenue` description from "Date (UTC)" to "Date (shop local, UTC+05:30)".

## 3. Test that would have caught it

Every existing test checks shape: `unique`, `not_null`, `accepted_values`. None checks that a value is correct. A timezone shift keeps every row valid, so all 40 tests passed.

Add `tests/order_date_matches_shop_local_day.sql`:

```sql
-- Known answers taken from the admin screen. Fails if order_date is
-- computed from UTC instead of shop-local time.
with expected as (

    select * from (values
        (73, date '2018-03-16'),
        (87, date '2018-03-27'),
        (89, date '2018-03-28')
    ) as t (order_id, expected_order_date)

)

select
    o.order_id,
    o.order_date,
    e.expected_order_date

from {{ ref('stg_orders') }} as o
join expected as e
    on o.order_id = e.order_id

where o.order_date <> e.expected_order_date
```

This fails on the current code (order 73 gives 2018-03-15) and passes after the fix. Confirm the three admin dates before committing the test. For 87 and 89 I derived them from the offset, not from the admin screen.

For broader coverage, export the admin's order dates to a seed and compare every order against it. That would catch the next shift, not just these three orders.

**Other things I noticed while reading the files (not verified):**
- `raw_customer_regions` has an `updated_at` column and no `unique` test on `customer_id`. If a customer can have more than one row, `region_revenue` double-counts revenue through the join. Add a `unique` test on `customer_id`.
- `customers` documents `total_order_amount`, but the model outputs `customer_lifetime_value`. The docs have drifted.
