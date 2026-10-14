claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 62 s | USD 0.010 | 2026-10-14 | project ad0b16b | prompt 3b4c114442ec

## 1. Root cause

**The `orders` count is inflated by duplicate rows in `raw_orders`.** The duplicates are most likely an ingestion problem: the same order was loaded twice under different `id`s. `stg_orders.sql` passes them through without deduplicating, and `orders.sql` carries them into `orders`, `customers`, and `region_revenue`. `daily_revenue` is then stale because of its incremental setup.

**Why the extra rows must be in `raw_orders`:** `orders.sql` is a left join from `stg_orders` onto `order_payments`, which is grouped by `order_id`. The join cannot multiply rows, and `stg_orders` has the same 115 rows as `raw_orders`. So the 16 surplus rows (115 vs 99) have to come from the raw table.

**Why daily_revenue is wrong in February, and why it looks right:** `daily_revenue.sql` only rebuilds `order_date >= max(order_date) - 3 days`. The duplicates have February order dates, far outside that window, so they never reach the dashboard. The dashboard's February rows were last built before the duplicates arrived. Its 99 / 1672 matches the admin and processor only because it is stale. Jan, Mar and Apr match `orders` exactly, and the entire gap is in February (43 vs 27 orders, 656.00 vs 415.00).

**Why the tests pass:** the `unique` tests are on `order_id`, and the duplicates have distinct ids. Nothing checks a natural key, and nothing reconciles `daily_revenue` against `orders`.

**Evidence in the samples:** `stg_orders` has order 41 and order 105, both customer 99, both at `2018-02-14 10:44:12`. Customer 51 has five orders in `customers` and three in the admin, which fits two extra rows for that customer.

I only have samples, so this is a strong hypothesis, not a confirmed diagnosis. The first query below is the decisive check.

**Confirm against the raw data:**

```sql
-- Should show 16 surplus rows, all in February
select user_id, ordered_at, count(*) as n, list(id) as ids, list(status) as statuses
from raw_orders
group by 1, 2
having count(*) > 1
order by ordered_at;

-- Should return 99
select count(distinct (user_id, ordered_at)) from raw_orders;
```

If the second query returns 99, the duplicates explain the whole gap. The first query also shows which `id`s are duplicates, which you need for the fix below.

## 2. Fix

**a) Deduplicate in staging, and fix the ingestion job upstream.** Choose the surviving row using the processor data, not just `min(id)`. Before merging, check that the kept row is the one that has settled payments in `raw_payments`. Also check for `raw_payments` rows that point at dropped `order_id`s.

```sql
-- models/staging/stg_orders.sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_orders') }}

),

deduplicated as (

    -- the ingestion job occasionally loads the same order twice under new ids;
    -- keep one row per customer and timestamp
    select *
    from source
    qualify row_number() over (partition by user_id, ordered_at order by id) = 1

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status

    from deduplicated

)

select * from renamed
```

**b) Make `daily_revenue` correct regardless of late data.** The table has about 69 rows, so a full rebuild is cheap. Simplest option:

```sql
-- models/daily_revenue.sql
{{
    config(
        materialized='table'
    )
}}

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

If you want to keep it incremental, the lookback has to cover how late data can arrive, not just payment lag. Until then, run `dbt build --select daily_revenue --full-refresh` once to repair the February rows.

**c) After the fix,** run `dbt build --full-refresh` for the affected models. Expect `orders` to show 99 rows and 1672.00, `customers.number_of_orders` to sum to 99, and customer 51 to show 3.

## 3. Test that would have caught this

**Primary: a natural-key uniqueness test on `stg_orders`.** It fails on the current data (the 16 surplus rows) and would have stopped the duplicates before they reached any model. This needs the `dbt_utils` package.

```yaml
# models/staging/schema.yml
  - name: stg_orders
    tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['customer_id', 'ordered_at']
```

**Second: a reconciliation test that catches the dashboard drift.** Save it as `tests/assert_daily_revenue_matches_orders.sql`. It returns a row for every date where the dashboard and `orders` disagree, so it fails in February today.

```sql
-- tests/assert_daily_revenue_matches_orders.sql
select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_in_orders,
    d.orders as orders_in_dashboard,
    o.revenue as revenue_in_orders,
    d.revenue as revenue_in_dashboard
from (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date
) o
full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

The strongest check would compare `orders` against the processor settlement and admin totals per day, which needs those figures loaded as a source or seed. dbt cannot see them on its own.

**Side note:** `customers.yml` documents `total_order_amount`, which the model no longer produces (it outputs `customer_lifetime_value`). Worth fixing while you are in there.

I haven't changed any files, since the project was pasted into this conversation rather than read from disk. If you point me at the repo, I can apply the staging fix, the `daily_revenue` change and the two tests, then run the build to confirm.
