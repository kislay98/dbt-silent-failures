claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 30 s | USD 0.075 | 2026-10-14 | project bef6b69

## 1. Root cause

**The raw feed was loaded twice for part of the data, and the second copy has new IDs.** Nothing in the project can detect this.

- **Duplicate orders.** `raw_orders` (feeding `stg_orders`) has 16 extra rows. For customers 1 and 51, orders 101, 102 and 114 are exact copies of 37, 38 and 50 (same customer, date, status and amount). Orders 37–52 appear to have been replayed as 101–116, which is consistent with the counts: 99 real orders + 16 = 115.
- **Duplicate payments.** `raw_payments` has a second set of payments for those orders, under new `id` values. This explains 1913 − 1672 = 241 of surplus revenue.
- **Why every test passes.**
  - `unique` on `order_id` and `payment_id` only checks the surrogate IDs, and the replayed rows have fresh ones.
  - `relationships` and `not_null` hold because the copies are internally consistent.
  - There is no test on the natural key (customer, `ordered_at`) or against the source system.
- **Why `daily_revenue` disagrees.**
  - It was last built with the 99 real orders, which is why it still shows 99 and 1672.00.
  - Its incremental filter in `daily_revenue.sql` only rebuilds `max(order_date) - 3 days`.
  - The replayed orders carry old dates (Jan–Feb), so they fall outside the window and the stale rows are never refreshed.
  - The comment in the model assumes late data lands within three days of the order date, but a replay can land at any date.
  - February days that contain a replayed order (Feb 10, 11, 13, 14, 16, 17, 19, 20, 21, 23, 24) are exactly half of `orders`. Feb 8, 25 and 26 match because they have no duplicates.
- **Customer 51.** Orders 25, 38, 50, 102 and 114 give five orders, but the admin has three (25, 38, 50).

**Secondary risk (unverified):** `raw_customer_regions` has an `updated_at` column but is documented as "one row per customer". If it holds history, `region_revenue` fans out on the join. Only `region` is tested for uniqueness, and there is no test on `customer_id`.

## 2. Fix

1. **Confirm the duplicates before deleting anything:**
   ```sql
   select d.id as dup_id, o.id as orig_id
   from raw_orders o
   join raw_orders d
     on d.user_id = o.user_id and d.ordered_at = o.ordered_at
    and d.status = o.status and d.id > o.id;
   ```
   Check that it returns 16 rows, and that the admin has no orders with these IDs.
2. **Remove the replayed rows at the source (the ingestion job or raw tables).** Fix the job so it is idempotent as well, otherwise this will recur.
   ```sql
   delete from raw_payments where order_id in (select d.id from raw_orders o join raw_orders d on d.user_id=o.user_id and d.ordered_at=o.ordered_at and d.status=o.status and d.id>o.id);
   delete from raw_orders  where id in (101,102,...,116);  -- the 16 ids from step 1
   ```
   Check the second set of payments against the processor settlement. The expected result is 99 orders and 1672.00.
3. **Rebuild the incremental model:** `dbt build --full-refresh -s daily_revenue+`. A normal run won't fix it, because of the three-day window.
4. **Make `daily_revenue` robust.** It is tiny, so make it a plain `table`. Alternatively, drive the lookback from a load timestamp instead of `order_date`.
5. **Check regions:** run `select customer_id, count(*) from raw_customer_regions group by 1 having count(*) > 1`. If there are duplicates, pick the latest row per customer by `updated_at` in a staging model.

I would not dedupe in `stg_orders` on (customer, `ordered_at`). That can silently drop legitimate orders, and the real fix is upstream.

## 3. Tests that would have caught it

**Natural-key uniqueness** in `models/staging/schema.yml`:
```yaml
  - name: stg_orders
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [customer_id, ordered_at]
```

**Reconciliation between the incremental model and its source** in `tests/assert_daily_revenue_matches_orders.sql`. This catches stale days that the unique and not_null tests can't:
```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
)
select coalesce(o.order_date, d.order_date) as order_date
from o
full join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

**Region feed uniqueness**, which closes the secondary risk:
```yaml
  - name: raw_customer_regions
    columns:
      - name: customer_id
        data_tests: [unique, not_null]
```

For the ops-level discrepancy, add a source-system reconciliation as well: a count or sum check against the admin or processor totals, for example a singular test against a small seed or exported control total.
