claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 23 s | USD 0.059 | 2026-10-14 | project bef6b69 | prompt 46732e565d43

## 1. Root cause

**`models/region_revenue.sql`** joins to `raw_customer_regions` as if it had one row per customer. The column list shows it doesn't: it carries an `updated_at` date, so it is a region history feed with one row per customer per region change. The `schema.yml` description ("One row per customer") is wrong.

`join regions on orders.customer_id = regions.customer_id` therefore fans out. A customer who has moved appears once in every region they've ever been in, and each of their payments is counted in each of those regions. That matches the symptoms:

- **Customers:** 68 against 62 means at least 6 extra customer-region pairs.
- **Orders:** 119 against 99 means 20 duplicated order rows.
- **Revenue:** 2028 − 1672 = 356 of double-counted revenue.

Finance wants each customer's current region, but the model uses all of them.

The build was green because the only tests on this path are `not_null`/`unique` on the output's `region` (which is still unique after the fan-out) and `not_null` on the seed. No test checks that the seed is unique per customer, or that the report reconciles to `orders`. The log also shows 27 tests ran but the project found 29, so the two seed tests on `raw_customer_regions` apparently didn't run in this build.

I haven't run anything against the warehouse. This is inferred from the schema and the numbers. To confirm:

```sql
select customer_id, count(*), list(region order by updated_at)
from raw_customer_regions group by 1 having count(*) > 1
```

## 2. Fix

Reduce the feed to one current row per customer before joining. I'd put this in a staging model, `models/staging/stg_customer_regions.sql`:

```sql
with source as (

    select * from {{ ref('raw_customer_regions') }}

),

ranked as (

    select
        customer_id,
        region,
        row_number() over (
            partition by customer_id
            order by updated_at desc, region
        ) as rn

    from source

)

select customer_id, region
from ranked
where rn = 1
```

In `region_revenue.sql`, change the `regions` CTE to `select * from {{ ref('stg_customer_regions') }}`. I'd also change the final join to `left join` with `coalesce(regions.region, 'unknown') as region`. As written, an inner join silently drops any customer with orders but no region row, and revenue wouldn't reconcile.

Update the seed description in `schema.yml` to say it holds region history, with one row per customer per change.

Expected result after the fix: revenue 1672.00, orders 99, customers 62.

## 3. Tests

**Uniqueness on the staging model** (in `models/staging/schema.yml`), which guards the grain of the join:

```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests: [unique, not_null]
```

**A reconciliation test** (`tests/assert_region_revenue_reconciles.sql`), which would have caught this directly:

```sql
with r as (
    select sum(revenue) as revenue, sum(orders) as orders, sum(customers) as customers
    from {{ ref('region_revenue') }}
),
o as (
    select
        sum(amount) as revenue,
        count(*) as orders,
        count(distinct customer_id) as customers
    from {{ ref('orders') }}
    where amount is not null
)
select *
from r, o
where abs(r.revenue - o.revenue) > 0.005
   or r.orders != o.orders
   or r.customers != o.customers
```

The test returns rows only when the report and `orders` disagree. Also make sure the seed/source tests actually run in the nightly build, since the log shows the two `raw_customer_regions` tests didn't.
