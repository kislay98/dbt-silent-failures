claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 71 s | USD 0.010 | 2026-10-14 | project bef6b69

## 1. Root cause

**`raw_orders` contains 16 duplicate order rows (ids 100–115), each a copy of an existing order with a new `id`. Their `raw_payments` rows are copies too.** The models pass these copies through, and the tests can't see them.

The data fits this:

- **Counts:** `orders` has 115 rows and the admin has 99, so 16 extra rows. The extra amount is 1913.00 − 1672.00 = 241.00.
- **Copies, not new orders:**
  - Customer 51: real orders 25, 38, 50. Order 102 duplicates 38 (2018-02-10, 15.0), and 114 duplicates 50 (2018-02-23, 26.0). That gives 5 rows against the admin's 3.
  - Customer 1: order 101 duplicates 37 (2018-02-10, 23.0).
- **`daily_revenue` on the mismatched days is exactly half of `orders`:** 02-10 is 6/94 vs 3/47, 02-17 is 6/68 vs 3/34, 02-21 is 4/36 vs 2/18. Every mismatched day is off by exactly its copies, and the per-day extras sum to 16. The dashboard is showing the pre-duplicate figures.

**Why the tests pass:** `unique` and `not_null` on `order_id` hold because the copies have fresh ids. No test checks a business key like (customer, timestamp, status), and nothing reconciles against the settlement or the admin.

**Files:** the bad data lives in `raw_orders` and `raw_payments`. Which file is wrong depends on the loader, and the materials don't settle that:

- **If the raw tables are seeds**, the bad rows are in `seeds/raw_orders.csv` and `seeds/raw_payments.csv`. This matters because the build log reports `4 seeds` and `PASS=40`, which equals 4 seeds + 7 models + 29 tests. The log starts numbering at 5, so the seed lines were cut out. If `dbt build` runs those seeds, it overwrites the ingested tables with the CSVs every night.
- **If ingestion loads them**, the replay or append step in the ingestion job is creating the copies.

I haven't run anything, so these are inferences from the excerpts. The first check is below.

## 2. Fix

**a) Confirm the set (read-only):**

```sql
select o.id, o.user_id, o.ordered_at, o.status
from raw_orders o
where exists (
    select 1 from raw_orders d
    where d.user_id = o.user_id
      and d.ordered_at = o.ordered_at
      and d.status = o.status
      and d.id < o.id
)
order by o.user_id, o.ordered_at;
```

Expect 16 rows, ids 100–115. Also confirm that `sum(amount)` over their `raw_payments` rows is 241.00 (in cents, 24100).

**b) Remove the copies at the source, after backing up:**

```sql
create table raw_orders_backup_20261014 as select * from raw_orders;
create table raw_payments_backup_20261014 as select * from raw_payments;
```

Then delete the 16 order ids and their payments from wherever the rows originate. If the source is the seed CSVs, edit `seeds/raw_orders.csv` and `seeds/raw_payments.csv`. If it's ingestion, fix the ingestion job. A warehouse-only delete will come back on the next load.

**c) Stop `daily_revenue` from going stale.** The 3-day lookback can't reach back to late-arriving data, so older days never heal. `orders` is already a full rebuild, so the incremental model buys nothing:

```sql
{{
    config(
        materialized='table'
    )
}}
```

Keep the rest of the model as it is. Then rebuild it once:

```bash
dbt build --select daily_revenue --full-refresh
```

**d) If the raw tables are seeds,** exclude them from the nightly job so ingested data isn't overwritten:

```bash
dbt build --exclude resource_type:seed
```

## 3. Tests that would have caught this

**a) Business-key duplicates (singular test, no packages needed).** `tests/assert_no_duplicate_orders.sql`:

```sql
select customer_id, ordered_at, status, count(*) as copies
from {{ ref('stg_orders') }}
group by 1, 2, 3
having count(*) > 1
```

This would fail today with three groups, covering customers 1 and 51. A genuine pair of identical orders at the same second would false-positive, so review any hit. If `dbt_utils` is installed, `unique_combination_of_columns` on the same columns is the equivalent.

**b) `daily_revenue` agrees with `orders` on every day.** `tests/assert_daily_revenue_matches_orders.sql`:

```sql
select coalesce(d.order_date, o.order_date) as order_date
from daily_revenue d
full outer join (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
) o on d.order_date = o.order_date
where d.orders is distinct from o.orders
   or round(d.revenue, 2) is distinct from round(o.revenue, 2)
```

This would have failed on the stale February days.

**c) Settlement reconciliation.** Ops' 1672.00 is the figure that would have caught this against an external number. It needs a daily settlement source (a seed or a source table) and a test that compares it to `sum(orders.amount)` by day.

## Other things I noticed

- **`region_revenue` may double-count.** `raw_customer_regions` has an `updated_at` column, and there's no `unique` test on `customer_id`. If the feed has more than one row per customer, the join fans out revenue. Check with `select customer_id from raw_customer_regions group by 1 having count(*) > 1`, and add a `unique` test on `customer_id`.
- **`customers` docs drift.** `schema.yml` documents `total_order_amount`, which the model doesn't produce; the model has `customer_lifetime_value`. This is docs-only, but it's misleading.
- **The `daily_revenue` comment is misleading.** It attributes the lateness to payments landing after orders. The actual problem is backfilled rows arriving for old dates.
