claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 25 s | USD 0.118 | 2026-10-14 | project f769fdf | prompt 9ea1d7d8a74d

# Diagnosis: duplicate customer rows in the regions seed inflate `region_revenue`

## 1. Root cause

The problem is in the **`raw_customer_regions`** seed and in how **`models/region_revenue.sql`** joins to it.

- The seed loaded **108 rows** (`INSERT 108`), but there are only **100 customers**.
- The table also has an `updated_at` column. That means it's a change feed with history, not "one row per customer" as `schema.yml` says. Some customers have more than one row.
- `region_revenue.sql` joins `payments → orders → regions` on `customer_id`. For a customer with N region rows, every one of their payments is repeated N times, so `sum(payments.amount)` is overstated by 1846 − 1672 = **174.00**.
- The counts look right because `count(distinct orders.customer_id)` and `count(distinct orders.order_id)` remove the fan-out. Only the `sum` is inflated.
- The duplicate rows evidently carry the **same region** (re-sent rows with a new `updated_at`). If any customer with orders had two different regions, their orders would be counted in both regions and `sum(orders)` would be above 99. It is exactly 99.

No test caught this. `raw_customer_regions.customer_id` only has `not_null`, with no `unique`. The `unique` test on `region_revenue.region` checks the output grain, not the join.

To confirm against the warehouse:

```sql
select customer_id, count(*) as n, count(distinct region) as regions,
       min(updated_at), max(updated_at)
from raw_customer_regions
group by customer_id
having count(*) > 1
order by customer_id;
```

The extra rows (n − 1 summed) should add up to 8, and the duplicated customers' payments should account for the 174.00.

## 2. Fix

Add a staging model that keeps the latest row per customer, and build `region_revenue` from it.

**`models/staging/stg_customer_regions.sql`** (new):

```sql
with source as (

    {#-
    The regions feed is loaded as a seed, like the other raw tables.
    It can contain several rows per customer; keep the most recent one.
    #}
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

**`models/region_revenue.sql`**: change the `regions` CTE to:

```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

This assumes "current region" is what the report wants. If finance needs the region *as of the order date*, use `updated_at` as a valid-from date and join on `orders.order_date` falling inside that row's validity window. With today's data both approaches give the same answer, because the duplicates don't change region.

## 3. Tests that would have caught it

**Grain tests.** Add to `models/staging/schema.yml`:

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

In `models/schema.yml`, fix the seed description. It is not one row per customer, so don't add `unique` there or it will fail by design. Put the uniqueness test on the staging model.

**Reconciliation test.** This catches any fan-out or dropped rows, wherever they come from. Add **`tests/assert_region_revenue_reconciles_to_orders.sql`**:

```sql
-- Revenue split by region must add up to total order revenue
with region_total as (
    select coalesce(sum(revenue), 0) as revenue from {{ ref('region_revenue') }}
),

orders_total as (
    select coalesce(sum(amount), 0) as revenue from {{ ref('orders') }}
)

select region_total.revenue as region_revenue, orders_total.revenue as orders_revenue
from region_total
cross join orders_total
where abs(region_total.revenue - orders_total.revenue) > 0.005
```

On the current data this test returns one row, 1846.00 vs 1672.00, and fails the build. After the fix it passes.

One related gap: `region_revenue` uses inner joins. A customer missing from the regions feed would have their revenue dropped silently. The `relationships` test above won't catch that, because it checks regions → customers, not the reverse. The reconciliation test does catch it in both directions.
