claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 22 s | USD 0.004 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`jaffle_shop/models/region_revenue.sql`** joins payments to orders and then to `raw_customer_regions` on `customer_id`. The `regions` CTE has no uniqueness guarantee on `customer_id`, so any customer with more than one row in the seed multiplies every one of their payments. The `count(distinct ...)` columns are immune to this, which is why customers and orders look right. `sum(payments.amount)` is not.

The seed has 102 rows (`INSERT 102` in the log) for 100 customers, so two customers appear twice. The 164 excess (1836 − 1672) is the payment total for those duplicated customers' orders, counted twice.

Two details suggest the duplicates share a region, which fits the symptoms: the customer counts are right, and the report says customers look right per region. If the duplicates had different regions, those customers would also be inflated in two regions' customer counts. I haven't seen the seed rows, so confirm with:

```sql
select customer_id, count(*) as n, count(distinct region) as regions
from raw_customer_regions
group by 1 having count(*) > 1;
```

Nothing catches this because:
- The seed's schema tests (`schema.yml`) only check `not_null` on `customer_id` and `region`, not `unique`.
- The `unique` test on `region_revenue.region` passes trivially, since the query groups by region.
- The `not_null` test on `revenue` doesn't check its value.
- The `updated_at` column in the feed is unused, so there is no "latest row wins" logic even though the feed is clearly versioned.

## 2. Fix

Dedupe the regions feed in a staging model, then compute revenue from the orders grain.

**New file `models/staging/stg_customer_regions.sql`:**

```sql
with source as (

    select * from {{ ref('raw_customer_regions') }}

),

latest as (

    select
        customer_id,
        region

    from source

    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

)

select * from latest
```

**`models/region_revenue.sql`:**

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
        count(distinct orders.order_id) as orders,
        sum(orders.amount) as revenue

    from orders

    join regions
        on orders.customer_id = regions.customer_id

    group by regions.region

)

select * from final
```

Switching to `orders.amount` is equivalent to the payments sum, because `orders.amount` is the sum of each order's payments. It also means a future fan-out in the regions feed can't silently multiply revenue.

If the two duplicate rows have **different** regions, `latest wins` is a business decision, not a technical one. Confirm with finance which region is authoritative before deploying.

Also add `stg_customer_regions` to `models/staging/schema.yml`, and update the seed's `schema.yml` if you keep it.

After deploying, run `dbt build --select stg_customer_regions+` and confirm `sum(revenue)` in `region_revenue` equals 1672.00.

## 3. Test that would have caught it

Add a uniqueness test on the regions grain. In `models/staging/schema.yml`:

```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
```

(Or put `unique` on `raw_customer_regions.customer_id` in the seed's `schema.yml` to fail earlier.)

The test that matches the actual symptom is a reconciliation test, since no per-column test would notice a revenue total drifting. Add `tests/assert_region_revenue_matches_orders.sql`:

```sql
select
    region_total,
    order_total

from (
    select
        (select sum(revenue) from {{ ref('region_revenue') }}) as region_total,
        (select sum(amount) from {{ ref('orders') }}) as order_total
) totals

where abs(region_total - order_total) > 0.005
```

This fails whenever regional revenue doesn't sum to total order revenue, whether the cause is duplicate mappings or customers with no region. The second case may be a legitimate gap rather than a bug, so if you have unmapped customers you'll want to decide whether they should fail the build or be reported as an "unassigned" region.
