claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 47 s | USD 0.166 | 2026-10-14 | project f769fdf | prompt 3dc6591beb12

# Diagnosis: replayed orders in the raw tables

## 1. Root cause

The bug is in the data, not the SQL. The ingestion job loaded **16 orders twice into `raw_orders`**, giving each copy a new `id`. It also loaded **their payments twice into `raw_payments`**, with new payment `id`s pointing at the new order ids.

The output sample shows the pattern:

| original | copy | customer | date | amount |
|---|---|---|---|---|
| 37 | 101 | 1 | 2018-02-10 | 23 |
| 38 | 102 | 51 | 2018-02-10 | 15 |
| 50 | 114 | 51 | 2018-02-23 | 26 |

Each copy has id = original + 64. That puts the copies at **ids 100–115**, duplicating **orders 36–51**. The numbers all fit this:

- **Order count:** 115 − 99 = 16 extra rows, which is exactly the number of ids from 100 to 115.
- **The February days:** the days that differ between `orders` and `daily_revenue` are Feb 10–24. On each of those days `orders` has exactly twice the dashboard count. The dashboard counts on those days add up to 3+1+1+1+1+3+1+1+2+1+1 = **16**. Feb 8, 25 and 26 are not doubled.
- **Amounts:** the dashboard revenue on those days adds up to 47+8+14+17+17+34+8+22+18+26+30 = **241**, and 1913 − 1672 = **241**. So each copy carries its own payments. Otherwise `not_null_orders_amount` would have failed for ids 100–115.
- **Customer 51:** their orders are 25, 38, 50, 102 and 114. The last two are copies, which leaves the 3 orders the admin shows.

**Why `dbt build` was green:** every test checks a surrogate key or a column's domain. The copies have new, unique, non-null ids, valid statuses and valid payment methods. Nothing checks for uniqueness on the *natural* key, such as `(user_id, ordered_at)`. `stg_orders.sql` and `stg_payments.sql` pass rows through without deduplicating.

**Why `daily_revenue` still shows 99 / 1672:** this is luck, not correctness. `models/daily_revenue.sql` only re-aggregates days where `order_date >= max(order_date) - 3 days`. The copies were inserted with February `ordered_at` values, far outside that window, so those days were never recomputed. The dashboard is right only because it is stale. The same filter means the model will silently ignore any backfill or correction older than 3 days.

## 2. Fix

**First, confirm the copies match on the natural key** (I inferred the duplication from the sample; I haven't queried the raw tables):

```sql
select d.id as dup_id, o.id as orig_id, o.user_id, o.ordered_at, o.status
from raw_orders d
join raw_orders o
  on  o.user_id    = d.user_id
  and o.ordered_at = d.ordered_at
  and o.id         < d.id
order by d.id;
-- expect 16 rows: dup_id 100..115 ↔ orig_id 36..51
```

**Then fix the data and the source:**

1. Delete the copies and their payments from the raw tables:
   ```sql
   delete from raw_payments where order_id in (/* dup_ids from the query above */);
   delete from raw_orders   where id       in (/* dup_ids from the query above */);
   ```
2. Fix the ingestion job so a replay upserts on the source's natural key instead of minting new ids. That is the real root cause, and it lives upstream of dbt.
3. Rebuild everything, and fully rebuild the incremental model so it can't carry stale history:
   ```bash
   dbt build --full-refresh -s daily_revenue+ && dbt build
   ```

**Optional guard in dbt** (`models/staging/stg_orders.sql`), so a future replay can't inflate the marts:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status

    from source

    -- the ingestion job has replayed orders under new ids; keep the first id per order
    qualify row_number() over (partition by user_id, ordered_at order by id) = 1

)
```

If you add this guard, `orders.sql` and `customers.sql` also need to filter payments to the surviving orders. Otherwise `customer_payments` puts the duplicate payments into a `null` customer group. Change `left join orders` to `join orders` there.

I'd still treat this guard as a backstop and rely on the tests below to fail loudly.

## 3. Tests that would have caught it

**Natural-key uniqueness on staging** (`models/staging/schema.yml`). This catches the problem directly:

```yaml
  - name: stg_orders
    data_tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['customer_id', 'ordered_at']
```

Without dbt_utils, you can write it as a singular test, `tests/assert_no_replayed_orders.sql`:

```sql
select customer_id, ordered_at, count(*) as n
from {{ ref('stg_orders') }}
group by 1, 2
having count(*) > 1
```

**Reconciliation between the incremental model and its source**, `tests/assert_daily_revenue_matches_orders.sql`. This also catches the stale-history problem in `daily_revenue`:

```sql
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
)
select coalesce(o.order_date, d.order_date) as order_date, o.orders, d.orders, o.revenue, d.revenue
from o
full outer join {{ ref('daily_revenue') }} d using (order_date)
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

On last night's run this would have failed with 11 rows, the Feb 10–24 days. The natural-key test would have failed with 16.

## Other things I noticed (not the cause)

- **`raw_customer_regions` may have several rows per customer.** It has an `updated_at` column, and there's no `unique` test on `customer_id`. If a customer has more than one row, `region_revenue` will double-count. Add `unique` on `customer_id`, or pick the latest row per customer.
- **`schema.yml` documents a column that doesn't exist.** It describes `customers.total_order_amount`, but the model outputs `customer_lifetime_value`.
