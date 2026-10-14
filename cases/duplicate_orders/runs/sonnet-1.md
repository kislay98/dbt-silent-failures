claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 32 s | USD 0.075 | 2026-10-14 | project bef6b69

## 1. Root cause

**The raw feed was loaded twice for part of the range, with new IDs the second time.** `raw_orders` and `raw_payments` (the `seeds/raw_orders.csv` / `raw_payments.csv` the ingestion job loads) contain a replayed block of 16 orders, ids 100-115, that repeats orders 36-51. The offset is exactly 64:

- 37→101: customer 1, 2018-02-10, 23.0
- 38→102: customer 51, 2018-02-10, 15.0
- 50→114: customer 51, 2018-02-23, 26.0

Their payments were replayed too, under new payment ids and with `order_id` pointing at 100-115. That is why the duplicates carry amounts and the total is 241.00 over the processor figure (1913 - 1672). It also explains customer 51 showing 5 orders instead of 3, and 115 orders against the admin's 99.

Every test passes because every test checks a technical key, not the business one. `unique(order_id)` and `unique(payment_id)` hold because the replay got fresh ids, `relationships` holds because the customers are real, and `not_null` and `accepted_values` hold because the values are valid. Nothing compares the data to an outside truth or checks the business key (customer, `ordered_at`, amount).

**`daily_revenue` is a second, separate problem, and it is why that table still shows 99 / 1672.00.** The model is incremental and its lookback window is `max(order_date) - 3 days`, which is relative to the newest order in the data, not to when late rows arrive. The replayed rows are dated in February, far behind the max date. So `delete+insert` never revisits those days, and the table keeps the numbers from the last run before the replay landed. In the sample, every February day with a duplicate shows exactly half of `orders` (6 vs 3, 2 vs 1, and so on). The comment in the model says late rows are handled, but only rows within 3 days of the newest order are.

I can't see the seed files, so confirm the duplicate block with:

```sql
select o.order_id, d.order_id as original
from raw_orders o join raw_orders d
  on d.user_id = o.user_id and d.ordered_at = o.ordered_at and d.status = o.status and d.id < o.id
```

## 2. Fix

**Data (and fix the ingestion job so it doesn't replay or re-id rows):**

```sql
delete from raw_payments where order_id between 100 and 115;
delete from raw_orders   where id       between 100 and 115;
```

Run the verification query first. Afterwards, expect 99 orders and 1672.00 in total.

**Rebuild the stale incremental table:**

```
dbt build --full-refresh -s daily_revenue+
```

**Make `daily_revenue` self-healing.** It is a small aggregate, so the simplest fix is to drop the incremental config and let it be a normal table, which the project default already does. If it must stay incremental, set the window from the load time rather than the data, or add a periodic full refresh. Corrections to old dates will otherwise never propagate.

**Latent issue to check:** `raw_customer_regions` has an `updated_at` column, and its description says "one row per customer", but only `not_null` is tested on `customer_id`. If it holds several versions per customer, `region_revenue` will fan out and over-count the same way. Add a `unique` test on that column (see below).

## 3. Tests that would have caught this

**Business-key duplicates in orders** (`tests/assert_no_replayed_orders.sql`):

```sql
select customer_id, ordered_at, status, count(*) as n
from {{ ref('stg_orders') }}
group by 1, 2, 3
having count(*) > 1
```

This would have caught the replay. To make it stricter, also group on the order's payment total.

**`daily_revenue` must reconcile with `orders`** (`tests/assert_daily_revenue_matches_orders.sql`):

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
)
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as d_orders
from o
full outer join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

This fails today for the February days. It catches any staleness in the incremental logic, not just this incident.

**Uniqueness on the regions feed**, in `schema.yml` under `raw_customer_regions.customer_id`:

```yaml
tests:
  - not_null
  - unique
```

A recommended addition is a source-level reconciliation: assert that `sum(orders.amount)` equals the processor settlement total for the period, or that the order count matches the shop admin's. That is the only check that would have caught the double load regardless of how the duplicate looked.
