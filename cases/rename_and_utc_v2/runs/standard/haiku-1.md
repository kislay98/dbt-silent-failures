claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 37 s | USD 0.007 | 2026-10-15 | project ad0b16b | prompt 15c0aae6d239

## 1. Root cause

**The data:** For 43 rows in `raw_orders`, `ordered_at` is NULL and the order timestamp sits in `order_ts`. Order 73 is one example: `ordered_at` is empty and `order_ts` is `2018-03-15 19:45:20`. The rows where both columns are populated, such as orders 25 and 46, have identical values, so `order_ts` looks like the same timestamp under a new column name. The ingestion job appears to have started writing the timestamp to `order_ts` and leaving `ordered_at` empty. I can't confirm that from the files. Check the ingestion job's source mapping.

**The model:** `jaffle_shop/models/staging/stg_orders.sql` derives everything from `ordered_at` alone:

```sql
ordered_at,
cast(ordered_at as date) as order_date,
```

When `ordered_at` is NULL, `order_date` is NULL. `orders` carries that NULL through (the 43 NULL-date rows in the monthly output), so every March and April order that arrived through the new column disappears from date-based reporting. `amount` is still correct (1672.00) because it comes from payments, not from the date.

**Why `daily_revenue` still shows March and April:** `daily_revenue` is incremental and only rebuilds the last three days of `order_date`. Earlier March and April rows were written on runs when `ordered_at` was populated, and nothing removes them. The model is therefore stale, not correct. Its March and April totals (35 and 8 orders) reflect the old load.

**Why the tests passed:** No test covers `orders.order_date` or `stg_orders.order_date`. The `not_null` tests on `amount` and the other amount columns pass because those values come from payments. The `daily_revenue` tests (`unique`, `not_null` on `order_date`) pass because NULL dates are never inserted into the incremental window, so nothing fails. Nothing compares the number of orders in `orders` with the number counted in `daily_revenue`.

**A second, separate issue to verify:** The model documents `order_date` as "Date (UTC)". Order 73's `order_ts` of 19:45:20 corresponds to 01:15:20 on the admin screen, which is a +05:30 offset. Ops reconciles against local time, so an order placed after the local-day boundary can land on a different date than the admin screen even with the NULL fixed. The sample doesn't show which convention `ordered_at` used before this load, and I can't determine it from the files. Check an order with a late-evening UTC timestamp against the admin screen before changing the timezone logic.

## 2. Fix

**Staging (`models/staging/stg_orders.sql`):** Fall back to `order_ts` when `ordered_at` is empty:

```sql
coalesce(ordered_at, order_ts) as ordered_at,
cast(coalesce(ordered_at, order_ts) as date) as order_date,
```

This restores the 43 dates. Decide the timezone question before merging. If the admin screen is the reference, `order_date` should be computed in shop local time, not UTC, and the description in `schema.yml` should change to match.

**Rebuild `daily_revenue` fully.** The incremental window only covers the last three days, so the recovered March orders would not be written otherwise:

```
dbt run --full-refresh -s daily_revenue
```

**Upstream:** Ask the ingestion owner whether `ordered_at` is being dropped and whether `order_ts` should be the source of truth. If so, backfill `ordered_at` in `raw_orders` so the coalesce is a fallback, not a permanent workaround.

**Downstream effects to expect:** `customers.first_order` and `most_recent_order` will fill in for customers whose orders had no date. Region revenue is unaffected.

## 3. A test that would have caught this

Add `not_null` on the date columns in `models/schema.yml` and `models/staging/schema.yml`:

```yaml
# models/schema.yml, under the orders model
      - name: order_date
        description: Date that the order was placed, from the ordered_at timestamp
        tests:
          - not_null
```

```yaml
# models/staging/schema.yml, under stg_orders
      - name: order_date
        tests:
          - not_null
```

Add a reconciliation test that compares the dated orders with `daily_revenue`. Create `tests/assert_daily_revenue_matches_orders.sql`:

```sql
select s.orders as source_orders, r.orders as revenue_orders
from (
    select count(*) as orders
    from {{ ref('orders') }}
    where order_date is not null
) s
cross join (
    select coalesce(sum(orders), 0) as orders
    from {{ ref('daily_revenue') }}
) r
where s.orders <> r.orders
```

Against the current data, this returns one row (56 dated orders vs 99 in `daily_revenue`), which is the failure the nightly build should have reported. After the fix and the full refresh, both sides should be 99 and the test should return no rows.
