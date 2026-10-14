claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 18 s | USD 0.057 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`seeds/raw_customer_regions.csv`, joined in `models/region_revenue.sql`.**

The regions feed is not one row per customer, although `schema.yml` describes it that way. The build log loads 102 rows into `raw_customer_regions` against 100 in `raw_customers`. The extra `updated_at` column shows it is a history-style feed, so some `customer_id`s appear more than once.

`region_revenue.sql` joins `payments → orders → regions` on `customer_id` and then computes `sum(payments.amount)`. Every payment of a duplicated customer is therefore counted once per duplicate row.

- `count(distinct customer_id)` and `count(distinct order_id)` ignore the fan-out, which is why customer and order counts look right.
- Summed `orders` is exactly 99, matching `orders`. So the duplicates repeat the same region for a customer, rather than moving them between regions. Otherwise that customer's orders would show up in two regions and the total would exceed 99.
- The 164.00 excess (1836 − 1672) is the payments of the duplicated customer(s) counted again.
- No test caught it because the only tests on the seed are `not_null`. Nothing checks `unique` on `customer_id`, and nothing reconciles `region_revenue` to `orders`.

I can't see the seed data, so confirm with:
```sql
select customer_id, count(*), list(region), list(updated_at)
from raw_customer_regions group by 1 having count(*) > 1;
```
The payments of the customers it returns should add up to 164.

## 2. Fix

Add a staging model that keeps only the latest region per customer, and point `region_revenue` at it.

`models/staging/stg_customer_regions.sql`
```sql
with source as (

    select * from {{ ref('raw_customer_regions') }}

),

latest as (

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

select * from latest
```

In `region_revenue.sql`, change the `regions` CTE to:
```sql
select * from {{ ref('stg_customer_regions') }}
```

If finance wants revenue attributed to the region at the time of the order, that needs an as-of join on `updated_at`. That is a separate design decision.

## 3. Tests

Add a uniqueness test on the staging model, and a test on the seed that fails on the raw feed. In `models/staging/schema.yml`:
```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
```

Add a reconciliation test, `tests/assert_region_revenue_matches_orders.sql`:
```sql
select r.revenue, o.amount
from (select sum(revenue) as revenue from {{ ref('region_revenue') }}) r
cross join (select sum(amount) as amount from {{ ref('orders') }}) o
where abs(r.revenue - o.amount) > 0.005
```

The unique test would have flagged the duplicated `customer_id`s directly. The reconciliation test would have failed on 1836 vs 1672. It will also fail if a customer is missing from the regions feed, since the inner join would drop their revenue. That is the other way this report can drift from settlement.

I haven't run any of this, because the project and seed files aren't in my working directory. Run `dbt build` after the change and confirm `sum(region_revenue.revenue) = 1672.00`.
