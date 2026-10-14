claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 33 s | USD 0.078 | 2026-10-14 | project bef6b69

## 1. Root cause

**The raw data has duplicated orders, and `daily_revenue` is too stale to show it.** Two separate things are wrong.

**a) Replayed rows in `raw_orders` and `raw_payments` (seed/ingestion data).**
- Orders 101, 102 and 114 are copies of orders 37, 38 and 50. They have the same customer, date, status and amount, and each id is exactly 64 higher than the original.
- That pattern fits a batch of orders 36-51 replayed under new ids 100-115. That is 16 extra orders, which is exactly 115 - 99.
- The payments were replayed as well. The duplicate orders carry the same amounts, which adds 241.00 (1913 - 1672).
- Every dbt test passes because they all check the wrong thing. `unique(order_id)` holds since the ids are new, and `relationships` and `not_null` hold since the rows are well-formed. There is no test on a business key such as customer + `ordered_at`, and no reconciliation test.
- Customer 51 has five orders instead of three because 102 and 114 duplicate 38 and 50. `customers.customer_lifetime_value` is inflated the same way.

I couldn't see the seed files, so I inferred the 100-115 range from the three visible pairs and the arithmetic. Confirm it against the data.

**b) `models/daily_revenue.sql` can't pick up backfilled history.**
- The incremental filter only rebuilds `max(order_date) - 3 days`. The replayed rows are dated February, so they never fall inside that window.
- `daily_revenue` still holds the numbers from before the replay (99 orders, 1672.00), while `orders` now reflects the duplicates.
- Fixing the raw data alone won't repair it either. Any late-arriving or corrected older row will leave it wrong.

**Possible third issue (unverified).** `raw_customer_regions` has an `updated_at` column, so it may hold several rows per customer. `models/region_revenue.sql` joins it on `customer_id`, which would fan out `revenue` and `orders`. The only tests on that seed are `not_null`, so nothing would catch it.

## 2. Fix

1. **Confirm the extra rows before deleting anything.** Check that these return exactly ids 100-115 and their payments, and that they match the admin:
   ```sql
   select o.id, o2.id as original_id
   from raw_orders o join raw_orders o2
     on o.user_id = o2.user_id and o.ordered_at = o2.ordered_at and o.id > o2.id;
   select * from raw_payments where order_id in (select id from raw_orders where id >= 100);
   ```
2. **Remove the replayed rows** from `raw_orders` and `raw_payments` at the source, and fix the ingestion job so it doesn't replay. Deleting is hard to reverse, so back up the tables or run it only after step 1 checks out.
3. **Defensive dedupe** in `stg_orders` and `stg_payments`, so a future replay can't inflate totals (keep the lowest id):
   ```sql
   select * from renamed
   qualify row_number() over (partition by customer_id, ordered_at order by order_id) = 1
   ```
   For payments, dedupe the same way on the original order's key, or dedupe after the orders filter.
4. **Make `daily_revenue` self-correcting.** It's a simple group-by, so the simplest fix is to drop the incremental config and use `materialized='table'`. If it must stay incremental, rebuild from a `loaded_at` watermark instead of `max(order_date)`.
5. **Rebuild:** `dbt build --full-refresh`.
6. **For the region join**, dedupe `raw_customer_regions` to the latest row per `customer_id` using `updated_at`, and add `unique` on `customer_id` to the seed.

After the rebuild, expect 99 orders, 1672.00 in `orders` and `daily_revenue`, and 3 orders for customer 51.

## 3. Tests that would have caught it

`tests/assert_no_duplicate_orders.sql` checks the business key:
```sql
select customer_id, ordered_at, count(*) as n
from {{ ref('stg_orders') }}
group by 1, 2
having count(*) > 1
```

`tests/assert_daily_revenue_matches_orders.sql` catches the stale incremental:
```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
),
d as (
    select order_date, orders, revenue from {{ ref('daily_revenue') }}
)
select coalesce(o.order_date, d.order_date) as order_date
from o full join d on o.order_date = d.order_date
where coalesce(o.orders, 0) <> coalesce(d.orders, 0)
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

`tests/assert_region_revenue_reconciles.sql` guards the join fan-out:
```sql
select 1
from (select sum(revenue) as r from {{ ref('region_revenue') }}) a,
     (select sum(amount) as r from {{ ref('orders') }}) b
where abs(a.r - b.r) > 0.005
```

Also add `unique` on `raw_customer_regions.customer_id` in `schema.yml`.

For the original incident, the business-key test would fail on the 16 replayed orders and the reconciliation test would fail on the February days, so the build would have gone red.
