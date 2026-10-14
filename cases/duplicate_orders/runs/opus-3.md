claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 41 s | USD 0.154 | 2026-10-14 | project f769fdf | prompt 3dc6591beb12

# Diagnosis: duplicated orders and payments in the raw data

## 1. Root cause

**The models are not the problem. `raw_orders` and `raw_payments` (loaded by the ingestion job) contain a duplicate batch of 16 orders under new ids, and each duplicate has its own copied payment rows.**

The `customer_id in (1, 51)` sample shows it directly:

| original | duplicate | customer | date | status | amount |
|---|---|---|---|---|---|
| 37 | 101 | 1 | 2018-02-10 | completed | 23.0 |
| 38 | 102 | 51 | 2018-02-10 | completed | 15.0 |
| 50 | 114 | 51 | 2018-02-23 | completed | 26.0 |

- Each duplicate has the same `user_id`, `ordered_at` date, `status` and amount as its original, and its id is offset by 64.
- The duplicates also carry payments. `orders.amount` is non-null for them, so `raw_payments` must contain copies of the original payments with new `payment_id`s and `order_id` pointing at 100+.
- The project's tests only check uniqueness of the surrogate keys `id` and `payment_id`, so the copies pass `unique_stg_orders_order_id` and `unique_stg_payments_payment_id`.

The numbers reconcile exactly:

- **`orders` vs admin:** 115 − 99 = 16 extra orders, and 1913.00 − 1672.00 = 241.00 extra amount.
- **`orders` vs `daily_revenue`, Feb 10–24:** the per-day differences add up to the same totals.
  - Extra orders: 3+1+1+1+1+3+1+1+2+1+1 = **16**.
  - Extra revenue: 47+8+14+17+17+34+8+22+18+26+30 = **241**.
- **Customer 51:** real orders are 25, 38 and 50. The copies 102 and 114 bring the count to 5.

**Why `daily_revenue` looks correct:** the incremental filter in `models/daily_revenue.sql` only rebuilds dates within 3 days of `max(order_date)`. The duplicates were loaded after their Feb 10–24 dates had dropped out of that window, so they never reached the table. It matches the admin by accident. This also shows the copies arrived as a late re-load, not with the original orders.

There is a latent issue as well: any late correction older than 3 days, such as a refund or a status change, will never reach `daily_revenue` either.

## 2. Fix

**Fix the data at the source, in the warehouse or in the ingestion job.** Look at what will be deleted before deleting anything:

```sql
-- 1. identify the re-loaded copies (keep the lowest id per natural key)
create temp table dup_orders as
select id from (
    select id,
           row_number() over (partition by user_id, ordered_at order by id) as rn
    from raw_orders
) where rn > 1;

-- 2. inspect: expect 16 rows (ids ~100–115) and ~241.00 of payments
select o.*, (select sum(amount) / 100 from raw_payments p where p.order_id = o.id) as amount
from raw_orders o where id in (select id from dup_orders) order by id;

-- 3. remove them
delete from raw_payments where order_id in (select id from dup_orders);
delete from raw_orders   where id       in (select id from dup_orders);
```

Then rebuild, forcing the incremental model to recompute every date:

```bash
dbt build --full-refresh
```

**Stop it recurring:** make the ingestion job idempotent. It should upsert on the source system's order key rather than appending with freshly minted ids. Find out why it re-sent the Feb 10–24 batch.

## 3. Tests that would have caught this

**`tests/assert_no_duplicate_orders.sql`:** uniqueness on the natural key, not just the surrogate id. This fails today with 16 rows.

```sql
select customer_id, ordered_at, count(*) as n
from {{ ref('stg_orders') }}
group by 1, 2
having count(*) > 1
```

**`tests/assert_daily_revenue_matches_orders.sql`:** reconciles the incremental model against its source. This fails today on 11 days. It also guards against the lookback window silently dropping late changes.

```sql
with expected as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
)
select coalesce(e.order_date, d.order_date) as order_date,
       e.orders as expected_orders, d.orders as actual_orders,
       e.revenue as expected_revenue, d.revenue as actual_revenue
from expected e
full outer join {{ ref('daily_revenue') }} d using (order_date)
where e.orders is distinct from d.orders
   or abs(coalesce(e.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

If the settlement or admin totals are available as a source, a third test comparing `count(*)` and `sum(amount)` in `orders` against them would be the strongest check.

**Two smaller things I noticed:**

- **`raw_customer_regions`** has an `updated_at` column but no `unique` test on `customer_id`. If the feed keeps history rows, `region_revenue` will double-count revenue (`sum(payments.amount)` fans out over the join). Add a `unique` test on that column.
- **`models/schema.yml`** documents a `customers.total_order_amount` column, but the model outputs `customer_lifetime_value`.
