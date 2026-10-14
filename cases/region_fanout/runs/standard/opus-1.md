claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 24 s | USD 0.179 | 2026-10-15 | project ad0b16b | prompt d232260b8f43

# Root cause: duplicate customers in the regions seed fan out the join in `region_revenue.sql`

## 1. Root cause

**Data:** `raw_customer_regions` should hold one row per customer. It has **108 rows for 100 customers**, so about 8 `customer_id`s appear more than once. The `updated_at` column shows this is a change-history feed (one row per region update), not a current-state snapshot.

**Code:** `models/region_revenue.sql` joins `payments → orders → regions` on `customer_id` and then runs `sum(payments.amount)`. Every payment for a duplicated customer is counted once per region row. That adds **174.00** (1846 − 1672).

**Why counts look right but revenue doesn't:**
- `customers` and `orders` use `count(distinct ...)`, which hides the fan-out.
- The per-region customer counts sum to 62, matching `customers.with_orders`. Order counts sum to 99, matching `orders`.
- So each duplicated customer's extra rows carry the *same* region. No customer is split across two regions; their payments are just counted twice (or more) within one region.
- `sum()` has no "distinct" protection, so only revenue is inflated.

**Why tests passed:** the seed only has `not_null` tests on `customer_id` and `region`, with no `unique` test. `region_revenue` only tests `region` uniqueness and `revenue` not-null. Nothing checks grain or reconciles totals.

To confirm the duplicates:

```sql
select customer_id, count(*) as n, list(region order by updated_at) as regions, list(updated_at order by updated_at) as updated
from raw_customer_regions
group by 1 having count(*) > 1
order by 1;
```

## 2. Fix

Add a staging model that reduces the feed to one row per customer, and build revenue from the order-grain `orders` model instead of raw payments.

**`models/staging/stg_customer_regions.sql`** (new)
```sql
with source as (

    {#-
    The regions feed is loaded as a seed, like the other raw tables.
    It is a change history (one row per update), so keep the latest row per customer
    #}
    select * from {{ ref('raw_customer_regions') }}

),

latest as (

    select
        customer_id,
        region,
        updated_at

    from source

    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

)

select * from latest
```

**`models/region_revenue.sql`**
```sql
with orders as (

    select * from {{ ref('orders') }}

),

regions as (

    select * from {{ ref('stg_customer_regions') }}

),

final as (

    select
        regions.region,
        count(distinct orders.customer_id) as customers,
        count(orders.order_id) as orders,
        sum(orders.amount) as revenue

    from orders

    join regions
        on orders.customer_id = regions.customer_id

    group by regions.region

)

select * from final
```

Using `orders` (one row per order, already reconciled to 1672.00) means a future fan-out would also show up in `count(orders.order_id)`, not just revenue.

Because the duplicates share a region, this should return exactly 1672.00 with the same per-region customer and order counts.

One question for finance: "latest region" assigns all historical revenue to the customer's current region. If they want revenue attributed to the region *at order time*, join on `updated_at <= order_date` and take the most recent such row instead.

## 3. Tests that would have caught this

**Grain test** in `models/staging/schema.yml`:
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
Put `unique` on the staging model, not the seed. The raw feed is legitimately a history and will always fail a uniqueness test.

**Reconciliation test** `tests/region_revenue_reconciles_to_orders.sql`. This catches any fan-out or dropped rows, whatever the cause:
```sql
with region as (

    select sum(revenue) as revenue, sum(orders) as orders from {{ ref('region_revenue') }}

),

orders as (

    select sum(amount) as revenue, count(*) as orders from {{ ref('orders') }}

)

select *
from region
cross join orders
where abs(region.revenue - orders.revenue) > 0.005
   or region.orders != orders.orders
```

Run against the current build, the reconciliation test would have failed with 1846 vs 1672. The `unique` test would have failed on the 8 duplicate `customer_id`s.
