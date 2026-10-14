claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 45 s | USD 0.006 | 2026-10-14 | project bef6b69 | prompt aaf31b99f700

## 1. Root cause

**The data is wrong at the source.** Eight March orders (ids 66–73) have `ordered_at = NULL` in `raw_orders`, the raw table the ingestion job loads. The dbt models pass that through without a guard.

- `stg_orders.sql` does `cast(ordered_at as date)`, so those rows get `order_date = NULL`.
- `orders.sql` keeps them. Its `left join` and `order_id` keys are fine, so the rows appear with a NULL `order_date`.
- The NULL bucket in the `orders` output is exactly 8 orders and 149.00, and 622.00 − 473.00 = 149.00 with 35 − 27 = 8. Those are the orders that drop out of "by order date" grouping.
- `customers.sql` then produces NULL `first_order` for customers whose only orders are in that set. Customer 36 has only order 66, and customer 19 has only order 73. Customer 79 has a February order too, so its `first_order` is populated. Its `number_of_orders = 2` still counts order 67, because `count(order_id)` ignores the date.
- The status values on these rows (`completed`, `shipped`) show they are real orders, not test data.

**Why the schema tests didn't catch it:** `schema.yml` declares `order_date` with only a description and no `not_null` test. The build log has no `not_null_orders_order_date` test, and `stg_orders` has no test on `ordered_at`. The `not_null` tests that do exist cover `order_id`, `customer_id`, and the amount columns, all of which are populated.

**Why the dashboard shows 622:** This is an inference. The sample doesn't include March rows from `daily_revenue`, so I can't see them directly. Two things do hold:
- `not_null_daily_revenue_order_date` passed in this build, so `daily_revenue` has no NULL-dated row now.
- `daily_revenue` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. March is never rewritten, so its rows keep whatever values were there before `ordered_at` went NULL.

So the dashboard most likely still holds the March figures from when those timestamps were populated. It agrees with the shop admin, which reads from the source system and still has the dates. The warehouse lost the timestamps during ingestion.

To confirm, run:
```sql
select sum(orders), sum(revenue) from daily_revenue where order_date between '2018-03-01' and '2018-03-31';
select count(*) from raw_orders where ordered_at is null;   -- expect 8, ids 66–73
```

**Files involved:**
- `raw_orders` (warehouse table, ingestion job): rows 66–73 have `ordered_at` NULL. This is the data fault.
- `models/staging/stg_orders.sql`: passes NULL through silently.
- `models/orders.sql`: no check on `order_date`.
- `models/customers.sql`: `first_order` is NULL for customers whose orders are undated.
- `models/daily_revenue.sql`: the 3-day incremental window means historical corrections never reach the dashboard.

## 2. Fix

**Data:**
1. Have the ingestion job reload `raw_orders` rows 66–73 with `ordered_at` from the source system. Check the ingestion logs for those ids to find out why the timestamps were dropped.
2. After the reload, confirm `select count(*) from raw_orders where ordered_at is null` returns 0.

**Code:**
3. Rebuild the stale dashboard once with `dbt run --full-refresh -s daily_revenue`. The incremental window won't correct March on its own.
4. Add the tests from section 3 so a repeat fails the build instead of shipping wrong numbers.
5. Optional: change `daily_revenue` to rebuild a longer window, or run a periodic full refresh. The current 3-day window is only safe if no late-arriving corrections exist.

I haven't changed any files. These steps depend on access to the ingestion job and the warehouse, which I don't have from here.

## 3. Test that would have caught this

Add a `not_null` test on `order_date` in `models/schema.yml` and on `ordered_at` in `models/staging/schema.yml`:

```yaml
# models/staging/schema.yml, under stg_orders.columns
      - name: ordered_at
        tests:
          - not_null
```

```yaml
# models/schema.yml, under orders.columns
      - name: order_date
        tests:
          - not_null
```

On the current data, both would fail with 8 rows, pointing at exactly the affected orders.

Also add a reconciliation check so that rows dropped or undated at ingestion show up as a failure. A singular test in `tests/` such as `tests/assert_no_undated_orders.sql` works:

```sql
select order_id from {{ ref('stg_orders') }} where ordered_at is null
```

If you already use `dbt_utils`, `equal_rowcount` between `raw_orders` and `orders` would also catch dropped rows.
