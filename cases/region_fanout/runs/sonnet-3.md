claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 16 s | USD 0.053 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`seeds/raw_customer_regions.csv`** has 108 rows for 100 customers (the log shows `INSERT 108`, versus `INSERT 100` for `raw_customers`). The `updated_at` column shows it is a history feed, not one row per customer. Some customers appear more than once, with an earlier and a later row.

**`models/region_revenue.sql`** treats the feed as one row per customer. `join regions on orders.customer_id = regions.customer_id` fans out every payment of a duplicated customer, once per region row. `sum(payments.amount)` then counts those payments several times.

This went unnoticed for these reasons:
- `count(distinct customer_id)` and `count(distinct order_id)` are fan-out-proof, so finance saw correct customer and order counts.
- The `unique` test on `region_revenue.region` checks the output grain, which is still one row per region. The seed only has `not_null` tests, with no `unique` on `customer_id`.
- `customers` and `orders` never touch the regions seed, so they still reconcile at 1672.00. The 174.00 gap (1846 − 1672) is the extra payments from duplicated customers.

I haven't been able to query the seed, so this is inferred from the row counts, the `updated_at` column and the symptom. Check with:

```sql
select customer_id, count(*), list(region order by updated_at) from raw_customer_regions group by 1 having count(*) > 1
```

## 2. Fix

Add a staging model that keeps the latest region per customer, and point `region_revenue` at it.

`models/staging/stg_customer_regions.sql`:
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

In `region_revenue.sql`, change the `regions` CTE to `select * from {{ ref('stg_customer_regions') }}`.

This uses each customer's current region for all their revenue. If finance wants revenue attributed to the region as of the order date, join on `updated_at <= order_date` instead. That is a business decision, so confirm it with them.

## 3. Tests

Add to `models/staging/schema.yml`, so the grain is enforced before the join:
```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
```

Also add a reconciliation test, `tests/assert_region_revenue_reconciles_to_orders.sql`, which would have caught this directly:
```sql
select
    r.revenue as region_total,
    o.revenue as orders_total
from (select sum(revenue) as revenue from {{ ref('region_revenue') }}) r
cross join (select sum(amount) as revenue from {{ ref('orders') }}) o
where abs(r.revenue - o.revenue) > 0.005
```

If some customers have no region row, this test would flag the missing revenue. In that case, change the join to a `left join` with `coalesce(region, 'UNKNOWN')`.

A `unique` test on the seed's `customer_id` in `schema.yml` would also have failed the build on the raw data.
