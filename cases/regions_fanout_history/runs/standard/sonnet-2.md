claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 19 s | USD 0.084 | 2026-10-15 | project ad0b16b | prompt dee3d414f358

## 1. Root cause

`raw_customer_regions` is not one row per customer. It has 106 rows for 100 customers, and the extra rows are older region assignments, since the table carries an `updated_at` column. The seed's description ("One row per customer") is wrong, and nothing enforces it.

`models/region_revenue.sql` joins it on `customer_id` alone:

```sql
join regions on orders.customer_id = regions.customer_id
```

Any customer with two region rows has every payment counted once per row, in both their old and their current region. That produces both discrepancies:

- **Customers:** 68 against 62. Six order-placing customers are counted in two regions each.
- **Revenue:** 2028 against 1672. The extra 356 is the payments of those customers, counted again.

Finance wants each customer's current region, so only the latest row per customer should be used.

The tests passed because none of them checks grain or reconciliation:

- `raw_customer_regions.customer_id` only has `not_null`, with no `unique`.
- `region_revenue` only tests `unique` on `region` and `not_null` on `revenue`, which a fan-out doesn't violate.
- The `unique` test on `region_revenue.region` holds, because the output is grouped by region.
- `orders` and `customers` are correct, which is why their totals match settlement.

## 2. Fix

Add a staging model that keeps the current region per customer. The `payment_id`-style tie-breaker is included so the result is deterministic if two rows share an `updated_at`.

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

In `region_revenue.sql`, replace the `regions` CTE body with `select * from {{ ref('stg_customer_regions') }}`.

Optionally, make the join a `left join` with `coalesce(regions.region, 'unknown')`. As written, an order from a customer missing from the feed silently drops out of the report.

Also correct the seed description in `schema.yml`, which should say one row per customer *per change*, with history.

Expected result after the fix: `sum(revenue)` = 1672.00 and `sum(customers)` = 62.

## 3. Tests that would have caught it

Add to `models/staging/schema.yml`:
```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
```
This guards the grain of the deduplicated model. Putting `unique` on the raw seed would simply fail on the history rows, which is the right signal for the feed but not for the pipeline.

Add a singular reconciliation test, `tests/assert_region_revenue_reconciles.sql`. It returns rows only on failure:
```sql
with r as (
    select sum(revenue) as revenue, sum(customers) as customers
    from {{ ref('region_revenue') }}
),
o as (
    select
        sum(amount) as revenue,
        count(distinct customer_id) as customers
    from {{ ref('orders') }}
    where amount is not null
)
select r.revenue, o.revenue as expected_revenue, r.customers, o.customers as expected_customers
from r, o
where abs(r.revenue - o.revenue) > 0.005
   or r.customers <> o.customers
```

Before the fix this test fails with 2028 against 1672 and 68 against 62. After the fix it passes, and it will also flag future fan-out or dropped customers.

I haven't run any of this, since I only have the files and output you pasted. Run `dbt build` and then check the reconciliation numbers above.
