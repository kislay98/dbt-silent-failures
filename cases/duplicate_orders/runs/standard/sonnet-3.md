claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 33 s | USD 0.104 | 2026-10-14 | project ad0b16b | prompt 3b4c114442ec

## 1. Root cause

**Data problem, in `raw_orders` (and the matching rows in `raw_payments`).** The ingestion job loaded 16 duplicate orders, ids 100 to 115. They are replays of orders already present under ids 1 to 99, with new ids but the same `user_id` and `ordered_at`. I inferred this from the samples rather than querying the full table. The evidence:

- `stg_orders` has order 41 and order 105, both customer 99 at `2018-02-14 10:44:12`.
- Customer 33's `first_order` is 2018-02-13 and they have 2 orders. Order 104 (customer 33, 2018-02-13 11:07:47) looks like a replay of an earlier order.
- The replayed orders are all dated 10 to 23 February, which is the run of days where `daily_revenue` disagrees. February has 43 orders in `orders` against 27 in `daily_revenue`, and 656 against 415. The difference is exactly 16 orders and 241.00.
- 115 − 16 = 99 orders and 1913 − 241 = 1672, which are the admin's and the processor's figures.
- Customer 51 has orders 25 and 114 in the samples, so 114 is probably one of the replays. Two replays would explain 5 orders against the admin's 3.
- Each replayed order also arrived with its own `raw_payments` rows. The processor never settled them, which is why settlement is 1672.

Nothing caught it because every test checks a technical key only. `unique(order_id)` passes since the replays have fresh ids, and `not_null`, `accepted_values` and `relationships` all pass too. Nothing checks the business key `(user_id, ordered_at)` or reconciles against an external total. `stg_orders.sql` and `stg_payments.sql` pass everything straight through, so the duplicates reach `orders`, `customers`, `region_revenue` (1913 total) and the rest.

**`daily_revenue.sql` is a secondary issue.** It is the only table showing the correct 99 and 1672, and that is by accident. It was built before the replay landed. It only rebuilds `order_date >= max(order_date) - 3 days`, which is early April, and the replays are dated February. The incremental model therefore never saw them. It is stale for any late-arriving or corrected data older than 3 days, so it will be wrong the next time that happens.

## 2. Fix

First confirm the duplicates:
```sql
select user_id, ordered_at, list(id order by id) ids
from raw_orders group by 1, 2 having count(*) > 1
```
This should return 16 rows, with the later ids being 100 to 115.

Then fix the staging layer. In `models/staging/stg_orders.sql`, keep the earliest id per business key:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status,
        row_number() over (partition by user_id, ordered_at order by id) as dedupe_rank

    from source

),

final as (

    select order_id, customer_id, ordered_at, order_date, status
    from renamed
    where dedupe_rank = 1

)

select * from final
```
In `models/staging/stg_payments.sql`, drop payments whose order was removed:
```sql
from source
where order_id in (select order_id from {{ ref('stg_orders') }})
```
For `daily_revenue`, the table is only 69 rows, so make it a plain `table` and remove the incremental block. If you keep it incremental, run `dbt build --full-refresh -s daily_revenue` once after this fix. Also stop relying on `order_date` for the lookback window.

Finally, fix the ingestion job so it stops replaying rows, and clear the 16 rows from `raw_orders` and `raw_payments` once ops confirm they are bogus. The staging dedupe is a safety net, not a substitute.

After the rebuild, `orders` should show 99 rows and 1672.00, February should be 27 orders and 415.00, and customer 51 should have 3 orders.

## 3. Tests

Business-key uniqueness, `tests/assert_orders_unique_per_customer_timestamp.sql`:
```sql
select customer_id, ordered_at, count(*) as n
from {{ ref('stg_orders') }}
group by 1, 2
having count(*) > 1
```
To catch it before the dedupe masks it, run the same check against `raw_orders` (`user_id`, `ordered_at`) as a source-level test.

Mart reconciliation, `tests/assert_daily_revenue_matches_orders.sql`. This would also have caught the stale incremental model:
```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
)
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as d_orders, o.revenue, d.revenue as d_revenue
from o
full join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```
Also consider a test that `sum(orders.amount)` equals `sum(customers.customer_lifetime_value)` and `sum(region_revenue.revenue)`. All three agreed on the wrong 1913, so only a check against an external control total, such as the processor settlement loaded as a seed, catches this class of error.
