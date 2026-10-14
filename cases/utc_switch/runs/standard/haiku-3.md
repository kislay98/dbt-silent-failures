claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 46 s | USD 0.008 | 2026-10-15 | project ad0b16b | prompt 63b47184243a

## 1. Root cause

`stg_orders.sql` derives `order_date` with `cast(ordered_at as date)`, which takes the calendar date of the raw timestamp as-is. The raw `ordered_at` is a naive `TIMESTAMP` with no zone, and the project treats it as UTC (`schema.yml`: "Date (UTC) that the order was placed"). The admin screen shows the shop's local time. Every downstream model inherits the UTC date: `orders.order_date`, `daily_revenue.order_date`, and `customers.first_order`/`most_recent_order`.

Order 73 shows the mismatch. The raw value is 2018-03-15 19:45:20, and the admin shows 2018-03-16 01:15:20, a shift of +05:30. Any order whose local time falls after midnight but whose UTC time is still the previous day lands on the wrong date. That affects only the orders in the UTC window where the two dates differ, so it shows up on some days and not others. Monthly totals still match because no order changes month.

Tests passed because nothing checks the date against the shop's clock. The `not_null` and `unique` tests can't detect a date that is consistently shifted.

Two things I could not settle from the material:

- **The offset doesn't match the rest of the project.** The +05:30 shift fits Asia/Kolkata or Asia/Colombo, but the regions (ACT, NSW, VIC, ...) and the AUD descriptions point to Australia, where March 2018 offsets were +10:30 to +11. Confirm which zone the admin screen uses before setting it.
- **Why it started in mid-March.** A fixed UTC-to-local conversion would have shown the mismatch from the start, not from mid-March. The likely explanation is that the feed changed how it writes `ordered_at` around 15 March, for example from local time to UTC. Check this by comparing the hour-of-day distribution of `ordered_at` before and after 15 March. If the feed mixes zones, no single conversion is correct and the feed has to be fixed at the source.

## 2. Fix

Make `order_date` the shop's local calendar day, with the zone set as a project variable.

`dbt_project.yml`:

```yaml
vars:
  shop_timezone: '<IANA zone the admin screen uses>'   # confirm before running; see note above
```

`models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        -- ordered_at is a naive UTC timestamp; order_date is the shop's local calendar day
        cast((ordered_at at time zone 'UTC') at time zone '{{ var("shop_timezone") }}' as date) as order_date,
        status

    from source

)
```

Also update the descriptions in `models/schema.yml`. `orders.order_date` and the `customers` date columns currently say "Date (UTC)", and they should say the shop's local date.

Then rebuild the chain with a full refresh. `daily_revenue` is incremental and only rebuilds the last three days, so without the full refresh the historical rows for March would stay wrong:

```
dbt build --select stg_orders+ --full-refresh
```

Side effects to be aware of: `customers.first_order` and `most_recent_order` will change for customers with orders near midnight, and `daily_revenue` will move counts between adjacent days.

## 3. Test that would have caught it

A unit test pins the conversion with a known case. It goes in `models/staging/schema.yml`, alongside the existing model definitions:

```yaml
unit_tests:
  - name: order_date_is_shop_local_day
    model: stg_orders
    given:
      - input: ref('raw_orders')
        rows:
          - {id: 73, user_id: 19, ordered_at: '2018-03-15 19:45:20', status: completed}
    expect:
      rows:
        - {order_id: 73, customer_id: 19, ordered_at: '2018-03-15 19:45:20', order_date: '2018-03-16', status: completed}
```

Run it with `dbt test --select stg_orders,test_type:unit`. Once the zone is confirmed, the expected value (`2018-03-16`) must match the admin screen.

For ongoing protection, add a reconciliation test that compares `daily_revenue` against a seed of the admin screen's daily counts for a sample of days, such as 26–28 March 2018 (5/3, 1/2, 0/1 before the fix). This catches the same class of error across the whole dataset, not just one order.

## Other issues I noticed (not the cause)

- `daily_revenue`'s three-day rebuild window only covers the most recent days in the table. Any late-arriving order older than that never gets corrected without a full refresh. The comment about payments is also wrong, since the window applies to orders.
- `schema.yml` documents `customers.total_order_amount`, but that column doesn't exist. The model outputs `customer_lifetime_value`.
