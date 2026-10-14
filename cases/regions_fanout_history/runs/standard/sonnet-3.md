claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 18 s | USD 0.085 | 2026-10-15 | project ad0b16b | prompt dee3d414f358

## 1. Root cause

**`seeds/raw_customer_regions` (the regions feed) is a history table, not one row per customer, and `models/region_revenue.sql` joins it as if it were one row per customer.**

- The feed has 106 rows for 100 customers and carries an `updated_at` column. Customers who changed region have one row per region, so the 6 extra rows are 6 region changes.
- `region_revenue.sql` runs `join regions on orders.customer_id = regions.customer_id` with no filter on `updated_at`. A customer with two region rows has every payment duplicated, once per row, and is counted in both regions.
- That explains both symptoms. Revenue is 2028 against 1672, so 356 is double-counted. Customers sum to 68 against 62, which is the 6 extra rows landing on customers who have orders.
- Finance wants each customer's current region, so only the latest row per customer should be used.
- None of the 35 checks could fail. `schema.yml` describes the seed as "One row per customer", but only `not_null` tests exist on it. Nothing tests uniqueness of `customer_id`, and nothing reconciles `region_revenue` to `orders`.

I haven't seen the seed rows, so I'm inferring the duplicates from the 106-vs-100 count, the `updated_at` column, and the arithmetic. I haven't run any SQL against the warehouse.

## 2. Fix

Add a staging model that keeps the latest row per customer, and point `region_revenue` at it.

`models/staging/stg_customer_regions.sql`:
```sql
with source as (

    {#-
    The regions feed is a history (one row per region change), so keep the
    current region only
    #}
    select * from {{ ref('raw_customer_regions') }}

),

current_region as (

    select
        customer_id,
        region,
        updated_at

    from source

    qualify row_number() over (
        partition by customer_id
        order by updated_at desc, region
    ) = 1

)

select * from current_region
```

In `region_revenue.sql`, change the `regions` CTE to `select * from {{ ref('stg_customer_regions') }}`. Also fix the seed description in `schema.yml` to say it is a history with multiple rows per customer.

Two things to check after the rebuild:
- **Missing customers:** the inner join to regions silently drops any customer with orders but no region row. If the reconciliation test below fails after the dedupe, switch to `left join` with `coalesce(region, 'unknown')`.
- **Tied `updated_at`:** the `region` tiebreaker only makes the choice deterministic. If two rows for a customer share the latest `updated_at`, ask the feed owner which is right.

Expected result: `sum(revenue)` = 1672.00, `sum(customers)` = 62, and `sum(orders)` = 99.

## 3. Tests

Add a uniqueness test on the staging model, in `models/staging/schema.yml`:
```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
```

Add a reconciliation test, `tests/assert_region_revenue_reconciles.sql`. It returns rows (a failure) when `region_revenue` disagrees with `orders` on revenue, orders, or customers:
```sql
with r as (
    select sum(revenue) as revenue, sum(orders) as orders, sum(customers) as customers
    from {{ ref('region_revenue') }}
),

o as (
    select sum(amount) as revenue, count(*) as orders, count(distinct customer_id) as customers
    from {{ ref('orders') }}
)

select *
from r
cross join o
where abs(r.revenue - o.revenue) > 0.005
   or r.orders != o.orders
   or r.customers != o.customers
```

The reconciliation test would have failed on the first run: 2028 vs 1672, and 68 vs 62 customers. The `unique` test would have flagged the 6 duplicate customers at staging.

Optionally, add the same `unique` test on `raw_customer_regions.customer_id` with `severity: warn`, so a feed that turns into a history is noticed at the source.
