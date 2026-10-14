claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 54 s | USD 0.009 | 2026-10-15 | project ad0b16b | prompt 11fb6c29d933

## 1. Root cause

**The ingestion job now writes new order timestamps to `raw_orders.order_ts` and leaves `raw_orders.ordered_at` NULL. `jaffle_shop/models/staging/stg_orders.sql` reads only `ordered_at`, so those orders get a NULL `order_date`.**

- **Raw data:** `raw_orders` has two timestamp columns, `ordered_at` and `order_ts`. Wherever both are populated (for example order 25 and order 46), they are identical. The 43 NULL `ordered_at` rows match the 43 NULL `order_date` rows in `orders`.
- **Staging:** `stg_orders.sql` has `ordered_at,` and `cast(ordered_at as date) as order_date`. It never looks at `order_ts`.
- **`orders`:** The 43 orders carry `order_date = NULL`. Their amounts (761.00) are still in the total, so the NULL bucket in the monthly query is exactly the March/April orders: 496 + 415 + 761 = 1672.
- **`daily_revenue`:** This is why it looks unchanged. It is incremental and filters `where order_date >= max(order_date) - 3 days`. NULL dates never pass that predicate, so the table keeps the March/April rows it already had. Those rows total 43 orders and 761.00, the same orders now missing from `orders`.
- **`customers`:** Customers whose orders are among the 43 show `number_of_orders` but NULL `first_order` / `most_recent_order` (customers 9, 47, 89, 21).

**Why every test passed:**
- `orders.order_date` and `stg_orders.order_date` have no `not_null` test. Nothing checks them.
- The `not_null` test on `daily_revenue.order_date` never sees the NULL rows, because the incremental filter drops them first.
- The `unique`/`not_null` tests on `order_id` are fine, because the orders themselves still exist.

**Timezone caveat:** `schema.yml` says `order_date` is "Date (UTC)", but ops reconciles against the admin screen in shop-local time and it agreed until this load. The overlapping rows suggest `order_ts` is on the same clock as `ordered_at`, which is the shop's local clock. I have not confirmed this for the new rows. See the check in section 2.

## 2. Fix

**Step 1: Confirm the clock of the new rows.** Run this first:

```sql
select order_id, ordered_at, order_ts from raw_orders where order_id = 73;
```

Order 73 is 2018-03-16 01:15:20 on the admin screen. In March 2018 the shop zone (assuming Australia/Sydney, AEDT, UTC+11) is 14:15:20 UTC on 2018-03-15.

- **If `order_ts` = `2018-03-16 01:15:20`:** `order_ts` is shop-local. Use the first version below.
- **If `order_ts` = `2018-03-15 14:15:20`:** `order_ts` is UTC. Use the second version. A plain coalesce would put this order on the 15th.

**Step 2: Fix the staging model** (`jaffle_shop/models/staging/stg_orders.sql`). Version for shop-local `order_ts`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ordered_at is NULL for newer orders; order_ts is the replacement feed
        coalesce(ordered_at, order_ts) as ordered_at,
        cast(coalesce(ordered_at, order_ts) as date) as order_date,
        status

    from source

)
```

Version if `order_ts` is UTC (convert to the shop zone before taking the date):

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- ordered_at is NULL for newer orders; order_ts is UTC and needs converting to shop time
        coalesce(ordered_at, (order_ts at time zone 'UTC') at time zone 'Australia/Sydney') as ordered_at,
        cast(coalesce(ordered_at, (order_ts at time zone 'UTC') at time zone 'Australia/Sydney') as date) as order_date,
        status

    from source

)
```

Replace `Australia/Sydney` with the zone the admin screen uses. `coalesce` prefers `ordered_at`, so the January and February dates do not change.

**Step 3: Rebuild `daily_revenue` from scratch.** The incremental model will not revisit older days on its own:

```
dbt build --full-refresh -s daily_revenue
dbt build
```

**Step 4: Fix the docs.** Once the zone is confirmed, change the `order_date` descriptions in `models/schema.yml` and `customers` to say shop local time, or UTC if the conversion version is used.

## 3. A test that would have caught it

Add `not_null` on the staging and output dates:

```yaml
# models/staging/schema.yml
  - name: stg_orders
    columns:
      - name: order_id
        ...
      - name: order_date
        tests:
          - not_null
```

```yaml
# models/schema.yml
  - name: orders
    columns:
      ...
      - name: order_date
        description: Date (shop local time) that the order was placed, from ordered_at, falling back to order_ts
        tests:
          - not_null
```

This test would have failed on the nightly build with 43 NULLs. dbt build skips the models downstream of a failing test, so `orders`, `customers`, and `daily_revenue` would not have been rebuilt with the bad data. The existing `not_null` on `daily_revenue.order_date` cannot catch this, because the incremental filter removes NULLs before the test runs. The `stg_orders` test is the earliest point where the problem is visible.
