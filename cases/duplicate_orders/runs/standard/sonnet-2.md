claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 29 s | USD 0.097 | 2026-10-14 | project ad0b16b | prompt 3b4c114442ec

## 1. Root cause

**The ingestion job loaded 16 duplicate orders (and their payments) into `raw_orders` and `raw_payments`.** The duplicates carry new ids, so every uniqueness test passes while the counts are wrong. I haven't seen the seed files, so this is inferred from the samples and totals.

- `raw_orders` has 115 rows against the admin's 99. The extra 16 are ids 100-115, all dated in February.
- Several of them repeat an earlier order for the same customer and timestamp. Order 105 and order 41 are both customer 99 at `2018-02-14 10:44:12`.
- Customer 51 has order 25 (Jan 28), which the admin counts. Order 114 (Feb 23) is the same kind of replay, and `customers` counts five orders against the admin's three.
- The totals match: `daily_revenue` shows 99 orders and 1672.00, which equals the admin and the processor settlement.
- `orders` is 16 orders and 241.00 higher than that, and the gap sits entirely in February (43 vs 27 orders, 656 vs 415 revenue).
- `customers` (115 orders, 1913.00) and `region_revenue` are inflated by the same rows.

**Why `daily_revenue` disagrees:** it is incremental with a 3-day lookback anchored on `max(order_date)`, which is in April. February is never rebuilt, so it kept the pre-duplicate numbers. It is correct only by accident. A `--full-refresh` today would make it wrong too.

**Why the tests miss it:** `unique` on `order_id` and `payment_id` can't see replays under new ids. Nothing checks the natural key (customer, `ordered_at`), and nothing reconciles `daily_revenue` to `orders`.

**Files involved:** `seeds/raw_orders.csv` and `seeds/raw_payments.csv` (the ingested data), plus `models/staging/stg_orders.sql` and `stg_payments.sql`, which don't guard against replays.

## 2. Fix

First confirm which rows are the replays:

```sql
select customer_id, ordered_at, list(order_id order by order_id) ids
from raw_orders group by 1, 2 having count(*) > 1
```

This should return 16 groups, and the later id in each should be in 100-115. Then:

- **At source:** have the ingestion job delete orders 100-115 and their payments, and make it idempotent on (`user_id`, `ordered_at`).
- **In dbt as a guard:** dedupe in staging.

`stg_orders.sql`, replacing the final `select * from renamed`:

```sql
deduped as (

    select
        *,
        row_number() over (partition by customer_id, ordered_at order by order_id) as rn
    from renamed

)

select order_id, customer_id, ordered_at, order_date, status
from deduped
where rn = 1
```

`stg_payments.sql`, to drop payments belonging to removed orders (add after `from source`):

```sql
    where order_id in (select order_id from {{ ref('stg_orders') }})
```

Check that the 16 duplicate orders' payments really are replays of the originals. If a duplicate carried a payment the original lacks, the settlement figure would tell you.

Then run `dbt build --full-refresh -s daily_revenue+ orders+ customers region_revenue`. The totals should come out at 99 orders and 1672.00 everywhere.

## 3. Tests that would have caught it

`tests/assert_raw_orders_natural_key_unique.sql` catches the replay at the source:

```sql
select customer_id_, ordered_at, count(*) as n
from (select user_id as customer_id_, ordered_at from {{ ref('raw_orders') }})
group by 1, 2
having count(*) > 1
```

`tests/assert_daily_revenue_matches_orders.sql` catches the stale incremental table. It would have failed in this very build:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
)
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as d_orders, o.revenue, d.revenue as d_revenue
from o
full join {{ ref('daily_revenue') }} d on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

A third option is a cross-model check that `sum(customers.number_of_orders) = count(*) from orders`. That only compares tables derived from the same bad data, so it wouldn't have helped here.
