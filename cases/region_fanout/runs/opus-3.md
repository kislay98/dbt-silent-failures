claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 27 s | USD 0.120 | 2026-10-14 | project f769fdf | prompt 9ea1d7d8a74d

## 1. Root cause

**`seeds/raw_customer_regions.csv` has more than one row for some customers, and `models/region_revenue.sql` joins to it as if it had one row per customer.**

The data shows this in three places:

- **Row counts.** The seed loaded **108 rows** (`INSERT 108`), but there are only **100 customers** (`raw_customers` loaded 100, and `customers` has 100 rows).
- **The extra column.** `raw_customer_regions` has an `updated_at` column. The feed is a change history (several rows per customer over time), not a snapshot. The seed description in `models/schema.yml` ("One row per customer") is wrong, and nothing tests it. The seed only has `not_null` tests.
- **The fan-out.** In `region_revenue.sql`, `payments → orders → regions` is a plain join on `customer_id`. Every payment for a customer with N region rows is counted N times in `sum(payments.amount)`. That gives 1846 instead of 1672, an overstatement of 174.

**Why the counts still look right:** `customers` and `orders` use `count(distinct ...)`, which hides the duplication. Total orders across regions is exactly 99, matching `orders`. So no order lands in two different regions. The duplicate rows for customers who have orders must carry the **same region** (for example, re-sent rows with a new `updated_at`). Only the non-distinct `sum(amount)` gets inflated. The `unique_region_revenue_region` test passes because grouping by region still yields one row per region.

To confirm in the warehouse:
```sql
select customer_id, count(*) as n, count(distinct region) as regions,
       list(updated_at order by updated_at) as versions
from raw_customer_regions
group by customer_id
having count(*) > 1;
```
You should see about 8 extra rows. If any of them show `regions > 1` for a customer with orders, my same-region inference is wrong, and finance's counts would also be off.

## 2. Fix

Deduplicate the feed once, in a staging model, and have `region_revenue` use it. I'm using the latest row per customer, which gives the customer's *current* region. If finance wants revenue attributed to the region in effect at order time, the join should instead be an as-of join on `updated_at <= order_date`. That's worth asking them.

**`models/staging/stg_customer_regions.sql`** (new):
```sql
with source as (

    {#-
    The regions feed is a change history: one row per customer per update.
    Keep only the latest row so this model is one row per customer.
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

After `dbt build`, `sum(region_revenue.revenue)` should be 1672.00, and the customer and order counts stay the same.

## 3. Tests that would have caught this

**Grain tests** in `models/staging/schema.yml`:
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
In `models/schema.yml`, also fix the seed description to say it's a history keyed by `(customer_id, updated_at)`. If you want, add a uniqueness test on that pair (for example `dbt_utils.unique_combination_of_columns`). A plain `unique` on `raw_customer_regions.customer_id` would have failed today with 8 duplicates, and that was the missing test.

**A reconciliation test**: `tests/assert_region_revenue_reconciles_to_orders.sql`. It catches *any* future fan-out or drop, including customers with orders but no region row, which the inner join would silently exclude:
```sql
-- Revenue by region must equal total order revenue (the finance-reconciled figure)
with region_total as (
    select coalesce(sum(revenue), 0) as revenue from {{ ref('region_revenue') }}
),

order_total as (
    select coalesce(sum(amount), 0) as revenue from {{ ref('orders') }}
)

select region_total.revenue as region_revenue, order_total.revenue as order_revenue
from region_total
cross join order_total
where abs(region_total.revenue - order_total.revenue) > 0.005
```

The grain test points at the root cause. The reconciliation test protects the number finance actually checks.
