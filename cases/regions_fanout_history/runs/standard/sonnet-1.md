claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 16 s | USD 0.082 | 2026-10-15 | project ad0b16b | prompt dee3d414f358

## 1. Root cause

`raw_customer_regions` is not one row per customer. It has 106 rows for 100 customers and carries an `updated_at` column, so it's a region **history** feed: customers who moved have one row per region they've lived in. The seed's description says "One row per customer", but nothing enforces it. Its schema only tests `not_null` on `customer_id` and `region`, so every test passes.

`models/region_revenue.sql` joins it directly:

```sql
join regions on orders.customer_id = regions.customer_id
```

Each customer with several region rows has all their payments counted once per row, under each old and new region. That fits the numbers:

- 106 − 100 = 6 extra rows, and all 6 belong to customers with orders.
- Customers per region add up to 68 instead of 62, which is those 6 extra (customer, region) pairs.
- Revenue is 2028 − 1672 = 356 too high, which is the duplicated payments of those 6 pairs.

Finance wants each customer's current region, so only the latest row per customer should be used. `orders`, `customers` and `daily_revenue` all reconcile at 1672, so the problem is limited to `region_revenue`.

## 2. Fix

Add a staging model that keeps the latest region per customer, and have `region_revenue` use it.

`models/staging/stg_customer_regions.sql`:
```sql
with source as (

    select * from {{ ref('raw_customer_regions') }}

),

ranked as (

    select
        customer_id,
        region,
        updated_at,
        row_number() over (
            partition by customer_id
            order by updated_at desc, region
        ) as rn

    from source

)

select customer_id, region, updated_at
from ranked
where rn = 1
```

In `region_revenue.sql`, change the `regions` CTE to `select * from {{ ref('stg_customer_regions') }}` and drop the seed comment.

The `region` tiebreak only makes the result deterministic. If two rows for a customer share the same `updated_at`, ask the feed owner which one is current.

## 3. Tests

In `models/staging/schema.yml`, assert that the staging model is one row per customer:
```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
          - relationships:
              arguments:
                to: ref('stg_customers')
                field: customer_id
```
A `unique` test on `customer_id` of the raw seed would also have caught this, but it would fail permanently on a history feed, so test the deduplicated model.

Add a reconciliation test, `tests/assert_region_revenue_reconciles.sql`. It returns rows (and so fails) if revenue or customer counts differ from the source models:
```sql
with r as (
    select sum(revenue) as revenue, sum(customers) as customers from {{ ref('region_revenue') }}
),
o as (
    select sum(amount) as revenue, count(distinct customer_id) as customers
    from {{ ref('orders') }}
    where amount is not null
)
select *
from r cross join o
where abs(r.revenue - o.revenue) > 0.005
   or r.customers <> o.customers
```
This also catches the opposite failure, where a customer with orders is missing from the regions feed and the inner join silently drops their revenue. If that happens legitimately, switch the join to a left join with a `coalesce(region, 'UNKNOWN')` bucket.

I haven't run any of this, since the project files aren't in my working directory. After applying it, `region_revenue` should show 62 customers and 1672.00 in total.
