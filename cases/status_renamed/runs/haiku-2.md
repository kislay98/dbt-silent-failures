claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 24 s | USD 0.004 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**The bug is in the raw data, and `stg_orders.sql` passes it through without checking.** The `raw_orders` seed has two status columns: `status` and `order_status`. `stg_orders.sql` reads only `status`:

```sql
cast(ordered_at as date) as order_date,
status
```

The monthly breakdown shows where it breaks. Every order from 2018-03 and 2018-04 has `status = NULL` (`with_status = 0`), and those are exactly the 43 NULL-status orders (35 + 8). Orders from 2018-01 and 2018-02 are fine. So the feed appears to have switched from `status` to `order_status` partway through, and the seed has both columns populated in different rows.

This explains the numbers:

- `orders.status` is NULL for 43 orders carrying $761 of amount. Some of those orders were completed in the source, but the completed bucket can't see them.
- `customers.completed_lifetime_value` is `sum(amount) where status = 'completed'`, so it drops by whatever completed revenue sits in the NULL rows. Last week's 1103 minus today's 824 leaves a gap of 279, which is probably the completed revenue in the NULL-status orders. I can't confirm that exact split from the sample, but it's consistent with the status-only read.
- `sum(orders.amount)` stays at 1672 because it doesn't filter on status, which is why finance sees the total unchanged.

**Why every test passes:** the `accepted_values` test on `status` in `stg_orders` and `orders` skips NULLs. `NULL` isn't in the list, so the test doesn't flag it. Nothing in the project tests `status` for `not_null`.

## 2. Fix

Stop reading only one column in staging. First confirm the two columns never disagree, so you know which one wins when both are populated:

```sql
select count(*) from raw_orders
where status is not null and order_status is not null and status <> order_status;
```

If that returns 0, update the staging model:

```sql
-- models/staging/stg_orders.sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        coalesce(status, order_status) as status

    from source

)
```

This is the fastest safe fix, and it doesn't touch the seed. The durable fix belongs upstream: the feed should emit one status column, so the `raw_orders` seed should be corrected to a single `status` column with the values populated. Until someone confirms the upstream rename, the `coalesce` is the right change.

After rebuilding, `completed_lifetime_value` should return to about 1103, assuming the NULL-status orders' completed statuses are in `order_status`. I haven't been able to check that against the full data, so verify the total after the build.

## 3. Test that would have caught it

Add `not_null` to `status` in staging and in the marts. It would have failed on the 43 NULL rows:

```yaml
# models/staging/schema.yml
  - name: stg_orders
    columns:
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

```yaml
# models/schema.yml, under orders
      - name: status
        tests:
          - not_null
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

Also add a `not_null` test on `customers.completed_lifetime_value`'s inputs, or a singular test that checks revenue reconciles across statuses. The `not_null` on status is the one that would have caught this specific failure.

## Other things I noticed

- **Possible fan-out in `region_revenue`:** `raw_customer_regions` has an `updated_at` column, which suggests there may be more than one row per customer. There's no `unique` test on `customer_id` in that seed. If there are duplicates, `region_revenue` joins them and double-counts revenue. Worth checking separately.
- **Documentation drift:** `customers` documents `total_order_amount`, but the model doesn't produce that column.
