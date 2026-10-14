claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 41 s | USD 0.207 | 2026-10-15 | project ad0b16b | prompt 11fb6c29d933

# Diagnosis: `orders` lost March and April because `ordered_at` is NULL for every order from March on

## 1. Root cause

**The data:** the ingestion job changed the shape of `raw_orders`. It added a new column, `order_ts`, and stopped filling `ordered_at` for new orders. In the `raw_orders` sample:

- Jan/Feb rows have both columns, with identical values (ids 25, 46).
- Every row from March on (ids 63, 65, 72, 77, 84, 93, 96) has `ordered_at = NULL`. The timestamp is only in `order_ts`.

**The code:** `jaffle_shop/models/staging/stg_orders.sql` still derives the date only from the old column:

```sql
cast(ordered_at as date) as order_date,
```

So all 43 March/April orders get `order_date = NULL`. That flows into `orders` and gives the NULL bucket in the monthly report (43 orders, 761.00 = 622 + 139). It also explains the 42 `customers` with NULL `first_order`. Amounts are untouched, which is why `sum(amount)` still reconciles to 1672.00.

**Why `daily_revenue` looks fine:** it is incremental. This run's filter is `where order_date >= (select max(order_date) - interval 3 day from {{ this }})`. A NULL `order_date` never satisfies that, so no rows were selected for rebuild. The March/April rows from earlier runs, built from `ordered_at` when it was still populated, were left in place. They are correct only by accident, and they will never update again.

**Why every test passed:** nothing checks `order_date` for nulls on `stg_orders` or `orders`. The only `not_null` on a date is on `daily_revenue.order_date`, and the incremental filter removed the nulls before they reached that table.

## 2. Fix

In `jaffle_shop/models/staging/stg_orders.sql`, fall back to `order_ts` when `ordered_at` is missing. Keep `ordered_at` as the preferred source, because it is the value ops has reconciled against since January.

Before writing the expression, check the one known data point:

```sql
select id, ordered_at, order_ts from raw_orders where id = 73;
```

**Case A: `order_ts = 2018-03-16 01:15:20`.** This matches the admin screen, so `order_ts` is shop-local time like `ordered_at`. Use:

```sql
    select
        id as order_id,
        user_id as customer_id,
        coalesce(ordered_at, order_ts) as ordered_at,
        cast(coalesce(ordered_at, order_ts) as date) as order_date,
        status

    from source
```

**Case B: `order_ts = 2018-03-15 14:15:20`.** This is UTC, since the shop is on AEDT, UTC+11. A plain cast would then put early-morning orders on the previous day and break ops' daily reconciliation. Convert to local time first:

```sql
        coalesce(ordered_at, timezone('Australia/Sydney', timezone('UTC', order_ts))) as ordered_at,
        cast(coalesce(ordered_at, timezone('Australia/Sydney', timezone('UTC', order_ts))) as date) as order_date,
```

Use the shop's actual zone if it is not Sydney or Melbourne. Order 73's offset tells you which one applies.

**Rebuild:** run `dbt build --full-refresh -s stg_orders+`. The full refresh matters because `daily_revenue` otherwise keeps its frozen March/April rows.

**Verify:** before the full refresh, save the current `daily_revenue` March/April rows. They were built from `ordered_at`, so they are a known-good reference. After the rebuild, per-day counts and revenue should match them exactly (March 35 / 622.00, April 8 / 139.00). Any day that shifts means the timezone handling is wrong.

**Docs:** `schema.yml` describes `order_date` as "Date (UTC)". Ops' results show it has always been the shop's local date, so correct the descriptions in `orders` and `customers`. Also ask the ingestion team whether `ordered_at` is deprecated. If it is, drop the `coalesce` once you have confirmed `order_ts` semantics.

## 3. Tests that would have caught it

Add `not_null` on `order_date` at the staging layer, so the build fails before any mart is built:

```yaml
# models/staging/schema.yml, under stg_orders
      - name: order_date
        tests:
          - not_null
```

```yaml
# models/schema.yml, under orders
      - name: order_date
        description: Date (shop local time) that the order was placed
        tests:
          - not_null
```

Also add a reconciliation test between `daily_revenue` and `orders`. This catches the incremental table drifting from its source, which a nulls-only check would miss. Create `tests/daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must agree with orders, per day and in total
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
)
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders, o.revenue, d.revenue
from o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date is not distinct from d.order_date
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

On this load, both `not_null` tests would fail with 43 rows. The reconciliation test would flag the NULL bucket, and the March/April days present in `daily_revenue` but missing from `orders`.
