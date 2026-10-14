claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 23 s | USD 0.168 | 2026-10-15 | project ad0b16b | prompt dee3d414f358

# Region revenue double-counts customers whose region has changed

## 1. Root cause

The bug is in **`models/region_revenue.sql`**. It joins `raw_customer_regions` as if that table had one row per customer, but it doesn't:

- `raw_customer_regions` has **106 rows for 100 customers**.
- It also has an `updated_at` column. That makes it a change history from the regions feed: a customer who moved region has one row per region they've been in.
- The description in `models/schema.yml` ("One row per customer") is wrong, and no test enforces it. `customer_id` only has `not_null`.

So in this join:

```sql
join regions on orders.customer_id = regions.customer_id
```

each payment from a customer with several region rows is counted once per row. The customer then appears in every region they've ever lived in, and their revenue is added to each one.

This matches both symptoms:

- **Customers:** there are 6 extra history rows, and the regions add up to 68 customers instead of 62. So the 6 duplicated customers all placed orders, and each one is counted in a second region.
- **Revenue:** 2028 − 1672 = **356**. That is those customers' payments counted a second time.

Every test still passed because the only test on `region_revenue` is `unique` on `region`, and the output is still one row per region. Nothing compares its totals to `orders`.

## 2. Fix

Reduce the feed to each customer's **current** region in a staging model, and use that in the report.

**`models/staging/stg_customer_regions.sql`** (new)
```sql
with source as (

    {#-
    The regions feed is loaded as a seed, like the other raw tables.
    It keeps a row per region change, so only the latest row per customer is kept
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

The `region` in the `order by` is only a tie-breaker so the result is deterministic. Check whether any customer has two rows with the same `updated_at`. If so, that is a feed problem to raise with whoever owns the feed.

**`models/region_revenue.sql`**: replace the `regions` CTE:
```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

The join still drops orders from customers who have no region row. If the report must total exactly 1672, change the join to a `left join` and use `coalesce(regions.region, 'Unknown') as region`. The test below catches that case too.

## 3. Tests that would have caught it

Add these to `models/staging/schema.yml`:
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

Fix the description of the seed in `models/schema.yml` to "One row per customer per region change". You can also add a `dbt_utils.unique_combination_of_columns` test on `[customer_id, updated_at]`.

Then add a reconciliation test that would have failed on the bad numbers (2028 vs 1672):

**`tests/region_revenue_reconciles_to_orders.sql`**
```sql
-- Revenue and customers by region must add up to the order totals
with region_totals as (

    select
        sum(revenue) as revenue,
        sum(customers) as customers

    from {{ ref('region_revenue') }}

),

order_totals as (

    select
        sum(amount) as revenue,
        count(distinct customer_id) as customers

    from {{ ref('orders') }}

)

select *
from region_totals
cross join order_totals
where abs(coalesce(region_totals.revenue, 0) - coalesce(order_totals.revenue, 0)) > 0.01
   or region_totals.customers != order_totals.customers
```

`unique` on the regions' `customer_id` stops duplicate rows from reaching the join. The reconciliation test catches any future fan-out or dropped rows in `region_revenue`, whatever the cause.
