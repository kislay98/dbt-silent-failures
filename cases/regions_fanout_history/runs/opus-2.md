claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 31 s | USD 0.128 | 2026-10-14 | project f769fdf | prompt dad92f6afadb

## 1. Root cause

The problem is in **`models/region_revenue.sql`**, which joins to `raw_customer_regions` as if it had one row per customer. It doesn't.

`information_schema` shows that `raw_customer_regions` has an **`updated_at`** column. The regions feed is a change history: when a customer moves region, a new row is appended. `models/schema.yml` still describes the table as "One row per customer from the regions feed", but nothing enforces that.

So the join `orders.customer_id = regions.customer_id` fans out. Every payment of a customer with N region rows is counted N times, once under each region they have ever had.

The numbers fit this exactly:

| | `region_revenue` | truth (`orders`) | inflation |
|---|---|---|---|
| revenue | 2028.00 | 1672.00 | +356.00 |
| orders (sum of per-region distinct counts) | 119 | 99 | +20 order-region duplicates |
| customers (sum of per-region distinct counts) | 68 | 62 | +6 customer-region duplicates |

The +6 customers and +20 orders mean those customers' orders appear under more than one region. Finance wants only the *current* region.

**Why the tests stayed green:**
- `unique_region_revenue_region` passes because the duplication happens *before* the `group by region`.
- `raw_customer_regions` has no `unique` test on `customer_id`.
- The two tests it does have never ran. The log says "Found … 29 data tests" but only 27 executed, and no seed nodes ran. Seeds are excluded from the nightly build because ingestion loads the raw tables, so the seed's tests were excluded with them.

To confirm against the warehouse:

```sql
select customer_id, count(*) as n, string_agg(region || '@' || updated_at, ', ') as history
from raw_customer_regions
group by customer_id
having count(*) > 1;
```

## 2. Fix

Add a model that resolves each customer to their latest region, and have `region_revenue` use it.

**`models/staging/stg_customer_regions.sql`** (new):

```sql
with source as (

    select * from {{ ref('raw_customer_regions') }}

),

current_region as (

    select
        customer_id,
        region,
        updated_at

    from source

    -- the feed keeps every region change; finance reports on the current region
    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

)

select * from current_region
```

**`models/region_revenue.sql`**: change the `regions` CTE to:

```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

Also update the seed description in `models/schema.yml` to "One row per customer per region change, `updated_at` is the date of the change".

Two caveats:
- If a customer can have two rows with the same `updated_at`, the latest row is ambiguous and you need a tiebreaker. The `unique` test below will surface this.
- `region_revenue` uses inner joins, so a customer with no region row would silently drop out. If the new totals come in *under* 1672.00, switch to `left join regions` with `coalesce(regions.region, 'Unknown')`.

## 3. Tests that would have caught it

Add a uniqueness test on the deduplicated model in **`models/staging/schema.yml`**. It runs in the nightly build, unlike the seed's tests:

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
      - name: region
        tests:
          - not_null
```

Add a reconciliation test, **`tests/region_revenue_reconciles_to_orders.sql`**. This one catches any future fan-out or drop-out regardless of cause:

```sql
-- region_revenue must add up to the same revenue, orders and customers as orders
with by_region as (

    select
        sum(revenue) as revenue,
        sum(orders) as orders,
        sum(customers) as customers
    from {{ ref('region_revenue') }}

),

by_order as (

    select
        sum(amount) as revenue,
        count(*) as orders,
        count(distinct customer_id) as customers
    from {{ ref('orders') }}
    where amount is not null

)

select *
from by_region
cross join by_order
where round(by_region.revenue, 2) != round(by_order.revenue, 2)
   or by_region.orders != by_order.orders
   or by_region.customers != by_order.customers
```

Against the current data this test returns a row (2028 vs 1672, 119 vs 99, 68 vs 62) and fails the build.

Separately, `raw_customer_regions` is loaded by ingestion, not by `dbt seed`. It should be declared as a `source` rather than a seed, with its tests under `sources:`. Source tests are picked up by `dbt build` even when seeds are excluded, and it removes the risk of a `dbt seed` run overwriting the ingested table with a stale CSV.
