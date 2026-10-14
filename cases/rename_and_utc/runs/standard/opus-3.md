claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 51 s | USD 0.104 | 2026-10-15 | project ad0b16b | prompt 11fb6c29d933

I couldn't verify anything against the warehouse: the `jaffle_shop` directory here is empty, and there's no DuckDB file, `dbt`, or `duckdb` binary. Everything below comes from the files and output you pasted. I've marked the one point that still needs a query.

## 1. Root cause

**The ingestion job changed the schema of `raw_orders`. The order timestamp now arrives in a new column, `order_ts`, and `ordered_at` is NULL for every order from March onwards. `jaffle_shop/models/staging/stg_orders.sql` still builds `order_date` only from `ordered_at`.**

The evidence:

- `information_schema` shows `raw_orders` now has a fifth column, `order_ts TIMESTAMP`.
- In the `raw_orders` sample, Jan and Feb rows have both columns filled with identical values (order 25: `2018-01-28 09:00:52` in both). Every March and April row (63, 65, 72, 77, 84, 93, 96) has `ordered_at = NULL` and only `order_ts` filled.
- `stg_orders.sql` does `cast(ordered_at as date) as order_date`, so those 43 orders get `order_date = NULL`. The NULLs flow into `orders` (the 43-row NULL month, whose 761.00 equals March 622 + April 139 in `daily_revenue`) and into `customers` (only 42 customers have a `first_order`).
- Amounts come from `raw_payments` and are unaffected. That's why `sum(amount)` is 1672.00 everywhere.
- Every test passed because no test checks `order_date` in `stg_orders`, `orders`, or the raw table. `daily_revenue` has `not_null` on `order_date`, but its NULL rows are filtered out before the test runs, so it can't fire.

`daily_revenue` only *looks* right. It's incremental, and its filter `where order_date >= (select max(order_date) - interval 3 day from {{ this }})` evaluates to NULL for the NULL-date rows. So this run inserted nothing for them, and the March/April rows are left over from earlier runs. Any new order would silently go missing there as well.

**Timezone, the one thing still to confirm.** Ops reconciles against the admin screen in shop-local time, and that has always matched. So the old `ordered_at` held local wall-clock time, even though `schema.yml` says "Date (UTC)". The Jan/Feb rows suggest `order_ts` uses the same convention, since both columns are equal there. However, that only proves it for rows where the ingestion filled both columns. Order 73 settles it:

```sql
select id, ordered_at, order_ts from raw_orders where id = 73;
```

- If `order_ts = 2018-03-16 01:15:20`, it's local time, and the fix below is complete.
- If `order_ts = 2018-03-15 14:15:20`, it's UTC (AEDT is UTC+11 in March). A plain cast would then put order 73, and every order placed between midnight and 11:00, on the previous day, which breaks the daily reconciliation. Use the UTC variant below.

## 2. Fix

In `jaffle_shop/models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        -- the loader moved the order timestamp from ordered_at to order_ts in March 2018;
        -- both hold the shop's local time
        coalesce(order_ts, ordered_at) as ordered_at,
        cast(coalesce(order_ts, ordered_at) as date) as order_date,
        status

    from source

)
```

If the order 73 check shows `order_ts` is UTC, convert only that column. Replace `Australia/Sydney` with the shop's actual zone (the regions span several states, so confirm it):

```sql
        coalesce(
            timezone('Australia/Sydney', order_ts::timestamptz)::timestamp,  -- order_ts is UTC
            ordered_at                                                       -- legacy, local
        ) as ordered_at,
```

Use the same expression inside the `cast(... as date)`. In DuckDB, check that `::timestamptz` interprets the value as UTC: set the session `TimeZone` to `UTC`, or use `order_ts AT TIME ZONE 'UTC'`. Whichever variant applies, also correct the "Date (UTC)" descriptions in `schema.yml` to say shop-local time, since that is what ops has been reconciling against.

Then rebuild everything, including `daily_revenue` from scratch, so no stale days remain:

```
dbt build --full-refresh -s stg_orders+
```

Afterwards, `orders` should show Jan 29, Feb 27, Mar 35, Apr 8 with no NULL month, and `customers` should show more than 42 customers with orders.

## 3. Tests that would have caught it

In `models/staging/schema.yml` (and the same on `orders.order_date` in `models/schema.yml`):

```yaml
  - name: stg_orders
    columns:
      - name: order_date
        tests:
          - not_null
```

This alone would have failed this run with 43 rows. Guarding the raw feed as well catches the column move at the source (add under `seeds:` in `models/schema.yml`):

```yaml
  - name: raw_orders
    tests:
      - dbt_utils.expression_is_true:
          arguments:
            expression: "coalesce(order_ts, ordered_at) is not null"
```

Two more guards, for the failure modes that were hidden here:

- `tests/daily_revenue_reconciles_with_orders.sql`: this catches the incremental model diverging from `orders`.
  ```sql
  select 1
  from (select sum(orders) as n, sum(revenue) as r from {{ ref('daily_revenue') }}) d,
       (select count(*) as n, sum(amount) as r from {{ ref('orders') }}) o
  where d.n != o.n or round(d.r, 2) != round(o.r, 2)
  ```
- A dbt unit test on `stg_orders` with order 73 as the fixture. It asserts `order_date = 2018-03-16` given the raw value(s) for order 73, which pins the timezone semantics so a UTC/local mix-up can't pass silently again.
