claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 23 s | USD 0.061 | 2026-10-14 | project bef6b69 | prompt 46732e565d43

## 1. Root cause

**`raw_customer_regions` (the seed/feed behind `models/region_revenue.sql`) is not one row per customer.** It has an `updated_at` column, so it is a history feed, and customers who changed region have one row per region they have been in. The schema describes it as "One row per customer", but nothing enforces that.

`region_revenue.sql` joins it on `customer_id` alone:

```sql
join regions on orders.customer_id = regions.customer_id
```

Every payment of a customer with *n* region rows is therefore counted *n* times, once under each region. That matches the symptoms:

- Customers: 68 vs 62 means 6 extra (customer, region) pairs.
- Orders: 119 vs 99 means 20 orders were duplicated.
- Revenue: 2028 vs 1672 means 356 was double-counted.

I can't see the seed data, so confirm with:

```sql
select customer_id, count(*), list(region order by updated_at)
from raw_customer_regions group by 1 having count(*) > 1
```

Every test passed because none of them checks the grain of the join:

- The only test on the regions feed is `not_null`, with no `unique` on `customer_id`. The log also shows those seed tests never ran, since only 27 of the 29 found tests executed.
- `unique(region_revenue.region)` is trivially true after `group by region`.
- Nothing reconciles the output to `orders`.

## 2. Fix

Reduce the feed to each customer's current region (latest `updated_at`) before joining. I'd put this in a staging model:

```sql
-- models/staging/stg_customer_regions.sql
with ranked as (

    select
        customer_id,
        region,
        updated_at,
        row_number() over (
            partition by customer_id
            order by updated_at desc nulls last, region
        ) as rn

    from {{ ref('raw_customer_regions') }}

)

select customer_id, region, updated_at
from ranked
where rn = 1
```

In `region_revenue.sql`, change the `regions` CTE to `select * from {{ ref('stg_customer_regions') }}`. Also make the join a left join so a customer with no region row doesn't silently drop revenue:

```sql
left join regions on orders.customer_id = regions.customer_id
...
coalesce(regions.region, 'UNASSIGNED') as region,
...
group by 1
```

Rebuild with `dbt build -s stg_customer_regions+`. Expected result: revenue 1672.00, orders 99, customers 62.

If two rows for a customer can share the same `updated_at`, the `region` tiebreaker is arbitrary. Ask the ingestion owner to fix that at the source.

## 3. Tests that would have caught it

Grain test on the staging model, which is built and tested on every run (the seed tests were not):

```yaml
# models/staging/schema.yml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests: [unique, not_null]
      - name: region
        tests: [not_null]
```

Reconciliation test that fails on any fan-out, even if the grain test is missed:

```sql
-- tests/assert_region_revenue_reconciles_to_orders.sql
with r as (
    select sum(revenue) as revenue, sum(orders) as orders, sum(customers) as customers
    from {{ ref('region_revenue') }}
),
o as (
    select sum(amount) as revenue, count(*) as orders, count(distinct customer_id) as customers
    from {{ ref('orders') }}
)
select *
from r, o
where abs(r.revenue - o.revenue) > 0.005
   or r.orders != o.orders
   or r.customers != o.customers
```

Before the fix, this test would have returned a row (2028 vs 1672, 119 vs 99, 68 vs 62) and failed the build.

I haven't run any of this, since the seed data and a dbt environment weren't available here.
