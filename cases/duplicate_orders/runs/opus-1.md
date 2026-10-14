claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 47 s | USD 0.164 | 2026-10-14 | project f769fdf | prompt 3dc6591beb12

# dbt diagnosis: 16 orders were ingested twice under new IDs

## 1. Root cause

**The bad data is in `raw_orders` and `raw_payments`, not the model SQL.** The ingestion job loaded a batch of 16 orders a second time and gave the copies new IDs. The payments for those orders were copied the same way.

The evidence for this is in the customer sample:

| original | copy | customer | date | amount |
|---|---|---|---|---|
| 37 | 101 | 1 | 2018-02-10 | 23.0 |
| 38 | 102 | 51 | 2018-02-10 | 15.0 |
| 50 | 114 | 51 | 2018-02-23 | 26.0 |

- **The copies follow a fixed pattern.** Each copy's ID is the original's ID plus 64, so orders 37–52 were reloaded as 101–116. IDs 101+ are dated in February, out of sequence with the rest of the table, which fits a later re-run of the load.
- **The February gaps add up exactly.** The per-day differences between `orders` and `daily_revenue` (Feb 10–24) total 3+1+1+1+1+3+1+1+2+1+1 = **16 orders**. Their amounts total 47+8+14+17+17+34+8+22+18+26+30 = **241.00**.
- **Those totals explain every symptom.** 115 − 99 = 16 extra orders, and 1913.00 − 1672.00 = 241.00 extra revenue. Customer 51 has 3 real orders plus 2 copies, which is the 5 you see.
- **The payments were duplicated too, also under new IDs.** The copies have non-null amounts equal to the originals, so `raw_payments` must contain payment rows pointing at orders 101–116. Those rows passed `unique_stg_payments_payment_id`, so their payment IDs are new as well.

**Why every test passed:**
- All the existing tests check surrogate keys (`order_id`, `payment_id`). Those are unique; the duplication is only visible on the natural key (`user_id`, `ordered_at`).

**Why `daily_revenue` is "right" and disagrees with `orders`:**
- `models/daily_revenue.sql` only rebuilds dates from `max(order_date) - 3 days` onward.
- The copies landed on Feb 10–24, older than that window, so the incremental model never picked them up. Feb 25–26 fall inside the window and do match.
- The dashboard is correct by luck. The same lookback would also hide a legitimate correction to older data.

## 2. Fix

**Step 1: confirm the duplicates.** This should return 16 rows, IDs 101–116, each paired with 37–52:
```sql
select dup.id as dup_id, keep.id as original_id, dup.user_id, dup.ordered_at, dup.status
from raw_orders dup
join raw_orders keep
  on  keep.user_id    = dup.user_id
  and keep.ordered_at = dup.ordered_at
  and keep.id         < dup.id
order by dup.id;
```

**Step 2: delete the copies, payments first.** Back up both tables before running this:
```sql
create table raw_orders_dupes as
select dup.id
from raw_orders dup
join raw_orders keep
  on keep.user_id = dup.user_id and keep.ordered_at = dup.ordered_at and keep.id < dup.id;

delete from raw_payments where order_id in (select id from raw_orders_dupes);
delete from raw_orders   where id       in (select id from raw_orders_dupes);
```

**Step 3: rebuild.**
```
dbt build --full-refresh -s +daily_revenue+ customers region_revenue
```
The full refresh of `daily_revenue` is needed whenever data older than 3 days changes.

**Step 4: fix the ingestion job.** Make the load idempotent, for example by merging or upserting on the source system's order key instead of appending with freshly generated IDs. Otherwise the next replay will duplicate the data again.

**Optional guard in `stg_orders`.** You can add this, but I would rely on the test in section 3 instead, so that a bad load fails loudly rather than being silently cleaned:
```sql
    from source
    qualify row_number() over (partition by user_id, ordered_at order by id) = 1
```
If you add it, `stg_payments` needs to drop payments whose `order_id` was removed.

## 3. Tests that would have caught it

**Natural-key uniqueness on orders.** Add this to `models/staging/schema.yml`:
```yaml
  - name: stg_orders
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['customer_id', 'ordered_at']
```
If you don't use dbt_utils, the same check as a singular test, `tests/assert_no_duplicate_orders.sql`:
```sql
select customer_id, ordered_at, count(*) as n
from {{ ref('stg_orders') }}
group by 1, 2
having count(*) > 1
```

**Reconciliation between the incremental model and its source**, `tests/assert_daily_revenue_matches_orders.sql`. This catches both the duplicates and the incremental model drifting:
```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
)
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as dr_orders,
       o.revenue, d.revenue as dr_revenue
from o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```
Either test would have failed last night: the first on 16 keys, the second on 11 February dates.

## Related issue to check

The log found 29 tests but ran only 27, and no seeds were run. That means the tests on `raw_customer_regions` aren't being run at all.

That table has an `updated_at` column, so it probably holds more than one row per customer. If so, the join in `region_revenue.sql` fans out:
- `count(distinct …)` hides the fan-out for customers and orders.
- `sum(payments.amount)` does not, so revenue would be overcounted.

Add a `unique` test on `customer_id` and run it. If it fails, filter `region_revenue` to the latest row per customer.
