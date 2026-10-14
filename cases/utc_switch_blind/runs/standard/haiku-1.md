claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 50 s | USD 0.008 | 2026-10-15 | project ad0b16b | prompt 9fc7feb7aef0

## 1. Root cause

**`stg_orders.sql` derives `order_date` from a naive UTC timestamp, while the admin screen and ops use India local time (UTC+05:30).** Any order placed after 18:30 UTC lands on the next local day in the admin screen but on the same UTC day in the warehouse.

The one-day shifts happen only in the evening window, so monthly totals still agree. Order 92 shows the mismatch directly:

- `raw_orders` / `stg_orders`: `ordered_at = 2018-04-01 20:55:29`, so `order_date = 2018-04-01`.
- In India that is 2018-04-02 02:25. The admin screen has one order on 04-02 and no row on 04-01, which matches local time exactly.

The code is consistent with its own docs: `schema.yml` describes `orders.order_date` as "Date (UTC)". The problem is that this doesn't match how the business counts days.

**Why it started in mid-March:** the code didn't change, so the input must have. My inference, which I can't confirm because the seed rows aren't in the materials, is that `raw_orders.ordered_at` changed convention around 2018-03-15. Earlier rows were India local time, so the cast gave the admin's date. Later rows are UTC, so the cast drifts for evening orders. `ordered_at` is a bare `TIMESTAMP` with no zone, which makes this kind of change invisible to the warehouse.

**Ruled out:** `daily_revenue` is not the cause. The log shows a full build, and its monthly totals match `orders`. `raw_customer_regions.updated_at` and the region join don't affect counts by day.

**To confirm the cutover:** ask the feed owner when the `ordered_at` convention changed, or check the ingestion changelog. You can also run this and look for the first day where the UTC and IST dates diverge in a way that matches the admin screen:

```sql
select
    ordered_at,
    cast(ordered_at as date)                      as utc_day,
    cast(ordered_at + interval 5 hour 30 minute as date) as ist_day
from raw_orders
where ordered_at >= timestamp '2018-03-01'
order by ordered_at;
```

## 2. Fix

I've assumed local India time is the correct basis, because that is what ops reconcile against. Note that this changes the documented meaning of `order_date` from UTC to local.

**a) Staging: derive the local date.** India has no DST, so a fixed offset is safe. This assumes all `ordered_at` values are UTC. If the pre-cutover rows are already local, you need the cutover:

```sql
-- stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at + interval 5 hour 30 minute as date) as order_date,
        status

    from source

)
```

If the cutover is real, the expression should use the cutover date (`<cutover>` is a placeholder I can't fill in from these materials):

```sql
case
    when ordered_at >= timestamp '<cutover>'
        then cast(ordered_at + interval 5 hour 30 minute as date)
    else cast(ordered_at as date)
end as order_date
```

Better still, normalise `ordered_at` at ingestion so staging doesn't carry the mixed convention.

**b) Force a full refresh of `daily_revenue`.** It is incremental and only rebuilds the last three days. Rows older than that will keep the old UTC counts:

```
dbt build --full-refresh -s stg_orders+ daily_revenue
```

**c) Update the docs.** In `orders` and `customers`, change "Date (UTC)" to "Date (IST)" for `order_date`, `first_order` and `most_recent_order`, and update the `daily_revenue` description to match.

## 3. Test that would have caught this

The existing tests (unique, not_null, accepted_values, relationships) can't detect a timezone shift, because the wrong dates are still valid dates. Two tests would catch it:

**a) A singular test that pins the local-day rule** (`tests/orders_order_date_is_local.sql`):

```sql
-- returns rows (failing the test) when order_date is not the India local date
select order_id, ordered_at, order_date
from {{ ref('orders') }}
where order_date <> cast(ordered_at + interval 5 hour 30 minute as date)
```

**b) A reconciliation test against the number ops actually use.** Load the admin screen's daily counts as a seed, `raw_admin_daily_orders` (`order_date`, `orders`), then add:

```sql
-- tests/daily_orders_match_admin.sql
select coalesce(d.order_date, a.order_date) as order_date, d.orders as warehouse, a.orders as admin
from {{ ref('daily_revenue') }} d
full outer join {{ ref('raw_admin_daily_orders') }} a using (order_date)
where d.orders is distinct from a.orders
```

Test (b) is the one that matches the reported symptom. Test (a) guards the rule itself.
