claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 44 s | USD 0.008 | 2026-10-15 | project ad0b16b | prompt 63b47184243a

## 1. Root cause

**The timestamps in `raw_orders.ordered_at` are in two different time zones, and `stg_orders.sql` assumes they are all in one.**

- **Order 73 shows the mismatch.** The admin screen shows 2018-03-16 01:15:20, and the warehouse has 2018-03-15 19:45:20. That is exactly a +05:30 offset, so the row is UTC while the admin screen is shop-local. Rows before the change appear to be shop-local, since they agreed with the admin screen. The change happened on or before 15 March 2018 19:45.
- **`models/staging/stg_orders.sql`** derives the date with `cast(ordered_at as date) as order_date`. That takes the calendar date of whatever the raw value is, with no time-zone handling. Any post-change order placed between midnight and the offset (local) gets a date one day early.
- **The day-level pattern matches this.** Orders from early-morning local time on 27 and 28 March land on 26 and 27 March in the warehouse. That gives 26 Mar +2, 27 Mar −1, 28 Mar −1, which sums to zero. Monthly totals still match because a shift within a month moves rows between days but not between months (the one exception is a month-end boundary).
- **The docs disagree with the ops process.** `schema.yml` says `order_date` is "Date (UTC)", but ops reconcile against shop-local time. The model follows neither consistently.
- **Why the tests passed:** every test is structural (`not_null`, `unique`, `accepted_values`, `relationships`). None compares a value against an independent source, so a one-day shift is invisible to them.

**Caveat on the offset:** +05:30 is not an Australian offset. In March 2018, Sydney and Melbourne were on AEDT (+11). Before you pick a time zone for the fix, confirm the zone configured on the admin screen. I have not verified this against the seed data, which you did not include, so the exact cutover timestamp is inferred.

## 2. Fix

**a) Normalise `raw_orders.ordered_at` to one zone (data).** Confirm the cutover with whoever owns the export. Then, for rows loaded before the cutover, convert from shop-local to UTC. Use the zone the admin screen is configured with, which I'll call `<shop_tz>`:

```sql
-- only for rows before the cutover, which are shop-local
(ordered_at at time zone '<shop_tz>') at time zone 'UTC'
```

Where the raw data comes from a seed, correct the affected rows in `seeds/raw_orders.csv` (or fix the export at the source). Afterwards, every `ordered_at` should be UTC.

**b) Make `stg_orders.sql` convert explicitly:**

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is UTC; order_date is the shop's local calendar date
        cast((ordered_at at time zone 'UTC') at time zone '{{ var("shop_timezone") }}' as date) as order_date,
        status

    from source

)
```

Set `shop_timezone` in `dbt_project.yml` under `vars:` with no default, so a missing value fails loudly. Update the `order_date` description in `models/schema.yml` to say "Date (shop local time)" if that is the meaning ops relies on.

**c) Full-refresh the downstream models.** `daily_revenue` is incremental and only rebuilds the last three days, so older wrong rows would survive a normal run:

```bash
dbt build --full-refresh -s stg_orders+
```

## 3. Test that would have caught it

Add a singular test that pins known admin-screen answers. Order 73 is the first one:

```sql
-- tests/assert_order_date_matches_admin_screen.sql
-- Expected dates were read off the admin screen (shop-local time).
-- Add a row each time ops finds a mismatch.
with expected as (
    select * from (values
        (73, date '2018-03-16')
    ) as t(order_id, expected_order_date)
)

select
    e.order_id,
    e.expected_order_date,
    o.order_date
from expected e
join {{ ref('orders') }} o using (order_id)
where o.order_date <> e.expected_order_date
```

To cover the daily discrepancies directly, load the admin's daily order counts as a seed (`admin_daily_orders.csv`) and add a singular test that fails when `daily_revenue.orders` differs from the seed for any day. That would have failed on 26, 27 and 28 March. The day-level tie-out is the check ops already does by hand, so it is the one to automate.

## Not verified

I did not run anything. I only had the pasted files and output, not the seed data or the admin screen's time-zone setting. The cutover date and the shop time zone are inferred from order 73 and the daily count differences, so confirm them before running the data fix.
