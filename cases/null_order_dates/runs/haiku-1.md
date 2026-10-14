claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 38 s | USD 0.006 | 2026-10-14 | project bef6b69 | prompt aaf31b99f700

## 1. Root cause

The bad data is in **`raw_orders.ordered_at`**, not in the models. Eight March orders, ids **66–73**, were loaded with `ordered_at = NULL`. The sample shows all eight with NULL `ordered_at` and NULL `order_date`, and they sum to **149.00**. That is the gap: 473.00 + 149.00 = 622.00, and 27 + 8 = 35 orders, which matches the shop admin.

The models pass the NULLs through without complaint:

- **`staging/stg_orders.sql`**: `cast(ordered_at as date) as order_date` turns NULL into NULL and keeps the row. Nothing filters it out or flags it.
- **`orders.sql`**: left-joins payments, so the eight orders keep their amounts but with a NULL `order_date`. They land in the NULL bucket of the by-date output (8 orders, 149.00).
- **`customers.sql`**: `min(order_date)` and `max(order_date)` ignore NULLs. Customers 19 and 36 each have only a NULL-dated order (73 and 66), so they show NULL `first_order` and `most_recent_order`. Customer 79 has one NULL-dated order (67) plus a dated one, so its dates look fine.

**Why the tests didn't catch it:**

- **`models/schema.yml`** and **`staging/schema.yml`** have no `not_null` test on `order_date` or `ordered_at`. Every test that exists on those models passes.
- **`daily_revenue.sql`** has a `not_null` test on `order_date`, and it passes only because the incremental filter `order_date >= ...` drops NULL dates on every run after the first. The test is checking a table that doesn't contain the bad rows, so it gives false comfort.
- The dashboard's 622 probably survives because `daily_revenue` is incremental and only rebuilds the last three days. March rows written while the dates were still populated were never rewritten. I can't confirm this from the sample alone, so check it with the query below.

I can't see how the ingestion job produced the NULLs. A likely cause is a timestamp that failed to parse and was coerced to NULL, but that needs the source system and the load logs.

## 2. Fix

**Data (the actual fix):**

1. Confirm the scope in the warehouse:
   ```sql
   select * from raw_orders where ordered_at is null;
   ```
   Expect ids 66–73, all in March 2018.
2. Find out why the ingestion job wrote NULL for those rows (source value, parse failure, or a bad cast) and correct the load. Re-load `ordered_at` for those eight orders, then check them against the shop admin.
3. Rebuild everything that depends on them, with a full refresh for `daily_revenue` so the incremental window can't keep stale March rows:
   ```
   dbt build --full-refresh -s daily_revenue+
   ```
4. Re-check March: `orders` should show 35 orders and 622.00, and `customers` should show dates for ids 19 and 36.

**Code (defence in depth, not the fix on its own):** don't silently drop NULL-dated orders in staging, because that would hide the problem. Make the build fail instead, with the test below.

## 3. Test that would have caught this

Add `not_null` on the date columns in the schema files. In `staging/schema.yml`:

```yaml
  - name: stg_orders
    columns:
      - name: order_id
        tests: [unique, not_null]
      - name: ordered_at
        tests:
          - not_null
      - name: order_date
        tests:
          - not_null
```

And in `models/schema.yml` under `orders`:

```yaml
      - name: order_date
        description: Date (UTC) that the order was placed, from the ordered_at timestamp
        tests:
          - not_null
```

With this in place, `dbt build` fails on the eight rows. `daily_revenue`'s existing `not_null` test is misleading, so it's worth adding a `dbt_utils.expression_is_true` or a singular test that checks the full-refresh output, not just the incremental run.
