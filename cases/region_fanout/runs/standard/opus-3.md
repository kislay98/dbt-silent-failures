claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 23 s | USD 0.175 | 2026-10-15 | project ad0b16b | prompt d232260b8f43

# Diagnosis: the regions seed has duplicate customers, so the region join double-counts payments

## 1. Root cause

The bad data is in **`seeds/raw_customer_regions.csv`**. The bug it triggers is in **`models/region_revenue.sql`**.

- `raw_customer_regions` loaded **108 rows**, but there are only **100 customers**. Its schema says "one row per customer", yet it also has an `updated_at` column. The feed contains repeated rows for some customers, either history rows or re-sends.
- `region_revenue.sql` joins `payments → orders → regions` on `customer_id` and then runs `sum(payments.amount)`. Each payment from a duplicated customer matches every one of that customer's region rows, so it is summed more than once. That accounts for the extra **1846 − 1672 = 174.00**.
- The counts survive because they use `count(distinct ...)`. They add up to 62 customers and 99 orders, matching `customers.with_orders = 62` and `orders = 99`. This also tells us the duplicate rows repeat the *same* region. If a customer had two different regions, they would appear in two regions and the totals would be above 62.
- No test caught it. `raw_customer_regions.customer_id` only has `not_null`, with no `unique`. `region_revenue` only tests `region`, `unique`/`not_null` and `revenue`, `not_null`. Nothing reconciles revenue against `orders`.

To confirm against the warehouse:

```sql
select customer_id, count(*) as n, count(distinct region) as regions
from raw_customer_regions
group by 1 having count(*) > 1 order by 1;
```

## 2. Fix

Dedupe the feed to the latest row per customer before joining. Do this in a staging model so every consumer benefits.

**`models/staging/stg_customer_regions.sql`** (new):

```sql
with source as (

    {#-
    The regions feed can send more than one row per customer; keep the latest
    #}
    select * from {{ ref('raw_customer_regions') }}

),

deduplicated as (

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

select * from deduplicated
```

**`models/region_revenue.sql`**: point the `regions` CTE at the staging model:

```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

Optionally, aggregate revenue from `orders.amount` rather than re-joining raw payments. Then the report shares one definition of revenue with `orders` and `daily_revenue`:

```sql
final as (

    select
        regions.region,
        count(distinct orders.customer_id) as customers,
        count(orders.order_id) as orders,
        sum(orders.amount) as revenue

    from {{ ref('orders') }} as orders

    join regions
        on orders.customer_id = regions.customer_id

    group by regions.region

)
```

After `dbt build`, `sum(region_revenue.revenue)` should equal 1672.00.

## 3. Tests that would have caught it

In `models/staging/schema.yml`, enforce the grain:

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

In `models/schema.yml`, also add `unique` to the seed's `customer_id`, set to `severity: warn`. This flags feed changes without blocking the build, since the staging model now handles duplicates:

```yaml
      - name: customer_id
        tests:
          - not_null
          - unique:
              config:
                severity: warn
```

Add a reconciliation test to catch any future fan-out or dropped rows, whatever the cause. For example, an inner join would silently drop orders from customers with no region. **`tests/region_revenue_reconciles_to_orders.sql`**:

```sql
-- Region revenue must add up to order revenue, which finance reconciles to settlement
with region_total as (

    select round(sum(revenue), 2) as revenue from {{ ref('region_revenue') }}

),

orders_total as (

    select round(sum(amount), 2) as revenue from {{ ref('orders') }}

)

select region_total.revenue as region_revenue, orders_total.revenue as orders_revenue
from region_total
cross join orders_total
where region_total.revenue != orders_total.revenue
```

On the current build, either the `unique` test or the reconciliation test would have failed: 1846.00 vs 1672.00.
