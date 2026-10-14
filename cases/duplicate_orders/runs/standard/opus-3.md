claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 44 s | USD 0.209 | 2026-10-14 | project ad0b16b | prompt 3b4c114442ec

## 1. Root cause

**The bad data is in `raw_orders` and `raw_payments` (seeds `raw_orders` / `raw_payments`, loaded by the ingestion job). It is not in the model SQL.** About 16 orders were ingested a second time under new surrogate ids, and each copy came with its own payment rows.

How the numbers line up:

- **Orders.** The admin shows 99 orders and `raw_orders` has 115. The extra 16 are ids 100–115. The sample shows one pair directly: order **41** and order **105** are both customer 99, `2018-02-14 10:44:12`, `completed`. Only the `id` differs.
- **Payments.** `raw_payments` has 131 rows. Payments 114–131 point at orders 100–115 (114→100, 127→113, 131→115). That adds 1913.00 − 1672.00 = **241.00** of revenue that was never settled.
- **Dates.** Every copy has a February `ordered_at`. In `orders`, February is 43 orders / 656.00. In `daily_revenue`, February is 27 / 415.00. The gap is exactly 16 orders and 241.00, and all other months match.
- **Customer 51.** They have orders 25 and 114 (2018‑02‑23) in the sample, so 5 orders in `customers` against 3 in the admin. The copies explain the extra two.

**Why every test passes.** Each copy has a new, unique, non-null `id` and valid status and payment method values. No test checks uniqueness on the business key (customer + timestamp).

**Why `daily_revenue` looks "right".** `daily_revenue.sql` is incremental and only rebuilds `order_date >= max(order_date) - 3 days`. The copies landed after February had already been built, with February dates, so the model never picked them up. The dashboard matches the admin only by accident. The same lookback would also silently drop any *genuine* late-arriving order. That is a second, latent bug in `models/daily_revenue.sql`.

Run this to confirm the full set of copies before deleting anything:

```sql
select o.user_id, o.ordered_at, list(o.id order by o.id) as ids
from raw_orders o
group by 1, 2
having count(*) > 1
order by 2;
-- expect 16 groups, each a (≤99, ≥100) id pair, all in February
```

## 2. Fix

**Primary fix (data):** have the ingestion owners delete the copies at the source and stop the job from re-inserting them:

```sql
delete from raw_payments where order_id in (/* the ≥100 ids from the query above */);
delete from raw_orders   where id       in (/* same ids */);
```

**Defensive fix (dbt):** deduplicate in staging so a repeat load can't inflate the marts.

`models/staging/stg_orders.sql`:
```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status

    from source

),

deduplicated as (

    -- the ingestion job has re-delivered orders under new ids; keep the first id per customer + timestamp
    select * from renamed
    qualify row_number() over (partition by customer_id, ordered_at order by order_id) = 1

)

select * from deduplicated
```

`models/staging/stg_payments.sql` drops payments that belong to the discarded copies:
```sql
renamed as (
    ...
),

valid_orders as (

    select order_id from {{ ref('stg_orders') }}

)

select renamed.*
from renamed
join valid_orders using (order_id)
```

Then rebuild, with a full refresh so `daily_revenue` is recomputed from the clean `orders`:

```
dbt build --full-refresh
```

Expected result: `orders` has 99 rows, `sum(amount)` is 1672.00, `customers.number_of_orders` sums to 99 with customer 51 at 3, and `daily_revenue` equals `orders` day by day.

Also widen `daily_revenue`'s incremental filter. Rebuilding by load or arrival time is best. A longer window, or a periodic full refresh, also works. Either way, late rows with old dates stop being silently skipped.

## 3. Tests that would have caught it

`tests/assert_orders_unique_on_business_key.sql`:
```sql
-- an order is one customer at one timestamp; a second id for the same pair means a duplicated load
select customer_id, ordered_at, count(*) as n
from {{ ref('stg_orders') }}
group by 1, 2
having count(*) > 1
```

`tests/assert_daily_revenue_matches_orders.sql` reconciles the incremental model with its source. It would have flagged the February disagreement, and it also catches the late-arrival bug on its own:
```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }} group by 1
)
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders as dr_orders, o.revenue, d.revenue as dr_revenue
from o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

If the project adds `dbt_utils`, the first test can be written in `staging/schema.yml` as `dbt_utils.unique_combination_of_columns` on `[customer_id, ordered_at]`.

One caveat: I confirmed only one duplicate pair directly from the sample (41/105). The other 15 are inferred from the matching counts and totals. Run the query in section 1 before deleting rows.
