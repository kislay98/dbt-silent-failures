claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 50 s | USD 0.225 | 2026-10-15 | project ad0b16b | prompt 15c0aae6d239

## 1. Root cause

**Data:** The ingestion job changed the shape of `raw_orders`. It added a new `order_ts` column and stopped populating `ordered_at` for new orders. All 43 orders from March onward have `ordered_at = NULL`. They are the same 43 rows that show up as `NULL` in the monthly report (35 + 8 = Mar + Apr in `daily_revenue`).

**Code:** `jaffle_shop/models/staging/stg_orders.sql` derives `order_date` only from the old column:

```sql
cast(ordered_at as date) as order_date,
```

As a result, `order_date` is NULL for those 43 orders in `stg_orders`, then in `orders`, and in `customers.first_order` / `most_recent_order`. Only 42 customers have a `first_order`, and customers like 9 and 47 have orders but no dates. Amounts are unaffected, which is why `sum(amount)` is still 1672.00. No test checks `order_date`, so the build stays green.

**Why `daily_revenue` looks fine:** it is incremental and its rows for March and April are left over from earlier runs. This run's incremental filter `order_date >= max(order_date) - 3 days` drops NULL dates, so the new batch was empty. `delete+insert` deleted nothing and inserted nothing. Those rows are now frozen: late payments for those days will never be picked up.

**`order_ts` is not a drop-in replacement.** It is in a different time zone than `ordered_at`:
- For Jan/Feb rows where both columns exist, they are identical (orders 25, 46, 47). So `ordered_at` was already shop-local time, which is why ops' reconciliation agreed every day. The `schema.yml` description "Date (UTC)" is wrong.
- Order 73: admin screen (local) shows 2018-03-16 01:15:20, `order_ts` shows 2018-03-15 19:45:20. That is a +05:30 offset.
- So `cast(order_ts as date)` would put order 73 on the 15th instead of the 16th. Every order placed between local midnight and 05:30 would land on the wrong day, and ops' reconciliation would break.

From one row I can't tell which zone `order_ts` is in. If it's UTC, the shop is on UTC+05:30 (e.g. `Asia/Kolkata`). That sits oddly with AUD and Australian regions, so **confirm both zones with the ingestion/app team before shipping**. Check especially whether the offset changes after 2018-04-01, when Australian DST ends.

## 2. Fix

**Preferred (data):** have the ingestion job backfill `ordered_at`, in the same local time as before, for the 43 rows and keep populating it.

**Code fix (works either way):** fall back to `order_ts` converted to shop-local time only when `ordered_at` is missing. Don't convert rows that have `ordered_at`, because for Jan/Feb `order_ts` already equals local time.

`jaffle_shop/models/staging/stg_orders.sql`:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ordered_at is shop-local; newer loads only fill order_ts, which is in a different zone
        coalesce(
            ordered_at,
            timezone('{{ var("shop_timezone") }}', timezone('{{ var("order_ts_timezone") }}', order_ts))
        ) as ordered_at,
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

`jaffle_shop/dbt_project.yml`:
```yaml
vars:
  # confirm with the app team; order 73 shows local = order_ts + 05:30
  order_ts_timezone: 'UTC'
  shop_timezone: 'Asia/Kolkata'
```

In DuckDB, `timezone(zone, timestamp)` reads a naive timestamp as being in `zone`, and `timezone(zone, timestamptz)` renders it as naive local time. This needs the ICU extension, which ships with DuckDB by default.

Also change the `order_date` descriptions in `schema.yml` from "(UTC)" to "shop local time".

Then rebuild:
```
dbt build --full-refresh -s stg_orders+
```
The full refresh is needed so `daily_revenue` is recomputed rather than left stale.

**Verify before trusting the conversion:**
- Order 73 should get `order_date = 2018-03-16`.
- The pre-rebuild `daily_revenue` rows for March/April were built from the old `ordered_at`, so they are a ground truth. Snapshot them first: `create table daily_revenue_before as select * from daily_revenue`. After the rebuild, compare per day. Any difference means the zone guess is wrong.

## 3. Tests that would have caught this

Add `not_null` on `order_date` in both `jaffle_shop/models/staging/schema.yml` (`stg_orders`) and `jaffle_shop/models/schema.yml` (`orders`):
```yaml
      - name: order_date
        tests:
          - not_null
```
This would have failed with 43 rows on this load.

Add a reconciliation test so the incremental table can't silently drift from its source, `jaffle_shop/tests/daily_revenue_matches_orders.sql`:
```sql
-- every order must land on a day in daily_revenue, with matching counts and revenue
with from_orders as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select *
from from_orders
full outer join {{ ref('daily_revenue') }} as daily_revenue
    using (order_date)
where from_orders.orders is distinct from daily_revenue.orders
   or round(from_orders.revenue, 2) is distinct from round(daily_revenue.revenue, 2)
```
On this load it would have returned the NULL-date group plus every March/April day.
