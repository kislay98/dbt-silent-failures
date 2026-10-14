claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 49 s | USD 0.007 | 2026-10-14 | project bef6b69

## 1. Root cause

**The duplicates come from the raw data, in `raw_orders` and `raw_payments`.** Nothing in the dbt project deduplicates them, and the tests can't see them.

- **Duplicate orders.** Orders 101, 102 and 114 repeat orders 37, 38 and 50 (same customer, order date, status and amount). Customer 51's five orders in `customers` are 25, 38, 50, 102 and 114. The admin's three are 25, 38 and 50.
- **Size of the problem.** 115 rows in `orders` minus 99 real orders is 16 extra rows. Those extra rows carry 241.00 of the difference between `sum(orders.amount)` (1913.00) and the settlement (1672.00). The three in the sample account for 64.00 of that.
- **Why the tests pass.** `unique` and `not_null` on `order_id` check the ID, and the duplicates have fresh IDs. `stg_orders.sql` passes `raw_orders` straight through, so the duplicates flow into `orders.sql` and `customers.sql`. The duplicates also have payment rows in `raw_payments`, since `orders.amount` for order 101 is 23.0. The processor didn't settle them, which is why settlement is 1672.00.

**`daily_revenue.sql` is stale, which is why the dashboard looks right.** Its incremental filter (`where order_date >= (select max(order_date) - interval 3 day from {{ this }})`) only rebuilds the last three days of the table. The duplicates landed on back-dated order dates (2018-02-10 and 2018-02-23), so those days were never rebuilt. The dashboard matches the admin only because it was built before the duplicates arrived. Feb 10 shows the pattern: `orders` has 6 rows and 94.00, `daily_revenue` has 3 rows and 47.00. Removing the two duplicates (101 and 102) gives 3 and 47.00 for that day, so the dashboard reflects the pre-duplicate state.

I worked this out from the sample output and did not query the raw tables. Before changing anything, confirm the duplicate key:

```sql
select user_id, ordered_at, status, count(*) as n
from raw_orders
group by all
having count(*) > 1;
```

You should get 16 extra rows in total. If the duplicates differ on `ordered_at`, the dedup key below needs to change.

Separately, `region_revenue.sql` joins `raw_customer_regions` without deduplicating it. That seed has an `updated_at` column, so a customer with more than one row will fan out and inflate `revenue` and `customers`. No test covers this. It's not the cause of the counts, but it needs the same fix.

## 2. Fix

**a) Deduplicate in staging** (`models/staging/stg_orders.sql`):

```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_orders') }}

),

deduped as (

    -- the ingestion job re-delivers some orders under new ids; keep the first id
    select
        *,
        row_number() over (
            partition by user_id, ordered_at, status
            order by id
        ) as row_num

    from source

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        status

    from deduped
    where row_num = 1

)

select * from renamed
```

**b) Only count payments for orders that survive** (`models/customers.sql`). `customer_payments` currently starts from `payments`, so payments for dropped duplicate orders would produce a NULL `customer_id` group. Start from `orders` instead:

```sql
customer_payments as (

    select
        orders.customer_id,
        sum(payments.amount) as total_amount,
        sum(case when orders.status = 'completed' then payments.amount else 0 end) as completed_amount

    from orders

    join payments on
         payments.order_id = orders.order_id

    group by orders.customer_id

),
```

`orders.sql` already joins from `stg_orders`, so it ignores the dropped payments.

**c) Make `daily_revenue` a table** (`models/daily_revenue.sql`). It is a cheap aggregate over `orders`, and the incremental window is what hid the problem. Replace the config block with `{{ config(materialized='table') }}` and remove the `is_incremental()` filter. If you need to keep it incremental, the window has to cover the whole table.

**d) Rebuild everything downstream** of the staging change, including the incremental model:

```
dbt build --select stg_orders+ --full-refresh
```

**e) Fix the regions seed.** Keep one row per customer, the latest by `updated_at`, in `region_revenue.sql` or in a staging model. Add a `unique` test on `customer_id` for `raw_customer_regions`.

**f) Fix the loader.** The duplicates are created upstream. If the ingestion job re-delivers orders under new IDs, it will keep doing so, and the dedup in staging is only a backstop.

## 3. Test that would have caught this

Add a singular test at `tests/assert_daily_revenue_matches_orders.sql`. It compares the dashboard model with `orders` day by day. It would have failed on Feb 10 and Feb 23 the first night the duplicates landed, and it catches both the duplicates and the stale window:

```sql
select coalesce(d.order_date, o.order_date) as order_date
from {{ ref('daily_revenue') }} as d
full outer join (
    select
        order_date,
        count(*) as orders,
        round(sum(amount), 2) as revenue
    from {{ ref('orders') }}
    group by order_date
) as o
    on d.order_date = o.order_date
where d.orders is distinct from o.orders
   or round(d.revenue, 2) is distinct from o.revenue
```

Also add a test that fails on duplicate orders, which pinpoints the cause. Put it at `tests/assert_stg_orders_natural_key_unique.sql`:

```sql
select customer_id, ordered_at, status, count(*) as n
from {{ ref('stg_orders') }}
group by 1, 2, 3
having count(*) > 1
```

If you already have `dbt_utils` installed, `dbt_utils.unique_combination_of_columns` on `stg_orders` does the same job. Your `packages.yml` isn't in the files you sent, so I wrote the singular test to avoid the dependency.
