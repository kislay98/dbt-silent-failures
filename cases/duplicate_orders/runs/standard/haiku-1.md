claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 50 s | USD 0.008 | 2026-10-14 | project ad0b16b | prompt 3b4c114442ec

## 1. Root cause

There are two faults, and together they produce the mismatch.

**a) Duplicate order rows in `raw_orders` (ingestion data).** The same order was loaded more than once under different `id`s. `stg_orders.sql` passes these through unchanged, so `orders` has 115 rows where there should be 99. Evidence:

- In the `stg_orders` sample, order 41 and order 105 both belong to customer 99 and both have `ordered_at = 2018-02-14 10:44:12`.
- The monthly breakdown shows all of the extra rows are in February: orders has 43 February orders and `daily_revenue` has 27, a gap of 16. January, March, and April match exactly.
- The February revenue gap is 656.00 − 415.00 = 241.00. That equals the 1913.00 − 1672.00 gap, so the duplicates carry payments and are also the source of the settlement mismatch.
- Customer 51 has 5 orders in `customers` against 3 in admin, so 2 of its orders are duplicates.

The `unique` and `not_null` tests on `order_id` pass because each duplicate has its own id. The tests check the wrong key.

**b) `daily_revenue.sql` never repairs old days (model logic).** The incremental filter is `order_date >= max(order_date) - 3 days` on the target table. Because the table already holds data through April, that cutoff stays in April, and February is never rebuilt. The duplicates reached `orders` after `daily_revenue` had built February, so the dashboard kept the pre-duplicate figures: 99 orders and 1672.00, which match admin and the settlement. The comment about payments landing late is why the window exists, but it does not cover late-arriving or corrected orders.

I haven't run queries against the warehouse, so confirm (a) before changing anything:

```sql
select user_id, ordered_at, count(*) as n, list(id order by id) as ids
from raw_orders
group by 1, 2
having count(*) > 1;
```

You should see 16 extra rows, all dated February. Then check that keeping the lowest `id` per pair reproduces the settlement:

```sql
select round(sum(p.amount) / 100, 2)
from stg_payments p
join (select min(order_id) as order_id from stg_orders group by customer_id, ordered_at) k
  on p.order_id = k.order_id;
```

This should return 1672.00. If it doesn't, the keeper rule needs adjusting (for example, keep whichever id the processor's payments reference).

## 2. Fix

**`models/staging/stg_orders.sql`** — drop the duplicate loads in staging. Payments for the dropped ids are ignored automatically, because `orders.sql` joins payments to orders by `order_id`.

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_orders') }}

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status,
        -- the ingestion job can load the same order twice under different ids; keep the lowest
        row_number() over (partition by user_id, ordered_at order by id) as row_num

    from source

),

deduped as (

    select order_id, customer_id, ordered_at, order_date, status
    from renamed
    where row_num = 1

)

select * from deduped
```

Also fix the loader so it is idempotent, for example by upserting on `(user_id, ordered_at)`, so that duplicates are not written to `raw_orders` in the first place.

**`models/daily_revenue.sql`** — the table has about 70 rows, so rebuild it in full each run. The incremental window adds risk and no benefit here.

```sql
with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

Update the `daily_revenue` description in `models/schema.yml` to remove "Incremental, rebuilds the last three days on each run". Then run `dbt build` once. Because the model is now a table, the February rows are rebuilt on that run.

While you are in `schema.yml`, `customers` documents a `total_order_amount` column that the model does not produce. The docs are wrong, and this is separate from the bug above.

## 3. A test that would have caught this

**Primary: a singular test on the natural key**, in `tests/assert_no_duplicate_orders.sql`. This fails on the source problem, before anything reaches the marts:

```sql
-- one order per customer per timestamp; returns rows (= failure) when duplicates exist
select customer_id, ordered_at, count(*) as n
from {{ ref('stg_orders') }}
group by customer_id, ordered_at
having count(*) > 1
```

**Second: a reconciliation test** in `tests/assert_daily_revenue_matches_orders.sql`. This would have caught the stale dashboard even if the duplicates had got through:

```sql
select 'orders' as check_name, o.n as expected, d.n as actual
from (select count(*) as n from {{ ref('orders') }}) o
cross join (select sum(orders) as n from {{ ref('daily_revenue') }}) d
where o.n <> d.n

union all

select 'revenue', round(o.r, 2), round(d.r, 2)
from (select sum(amount) as r from {{ ref('orders') }}) o
cross join (select sum(revenue) as r from {{ ref('daily_revenue') }}) d
where round(o.r, 2) <> round(d.r, 2)
```

The settlement figure comes from the processor, not from dbt. To check it automatically, you would need the settlement data loaded as a source and compared against `stg_payments` in the same way.
