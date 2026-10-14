claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 24 s | USD 0.177 | 2026-10-15 | project ad0b16b | prompt d232260b8f43

# Root cause: duplicate customers in the regions seed fan out the revenue join in `region_revenue`

## 1. Root cause

**Data:** `seeds/raw_customer_regions.csv` has more than one row for some customers. The schema describes it as "one row per customer", but it loaded **108 rows for 100 customers**. It also has an `updated_at` column, so it is really a change history: a customer gets a new row each time their region record is updated.

**Code:** `models/region_revenue.sql` joins payments to that seed on `customer_id` with no deduplication:

```sql
join regions
    on orders.customer_id = regions.customer_id
```

Every payment for a customer with N region rows is counted N times in `sum(payments.amount)`. That is where the extra **174.00** comes from (1846 − 1672).

**Why the counts look right but revenue doesn't:**
- `customers` and `orders` use `count(distinct ...)`, so the duplicated rows collapse back to the right numbers. They add up to 62 customers and 99 orders, which match `customers.with_orders` and `orders`.
- `revenue` uses a plain `sum`, so the duplicated rows are all added.
- The 62 total also tells us that, for customers with orders, the duplicate rows repeat the same region. Otherwise a customer would be counted in two regions and the total would be above 62. So the effect is a pure fan-out, not misattribution between regions.

**Why every test passed:**
- `raw_customer_regions.customer_id` has only a `not_null` test, with no `unique` test.
- `unique_region_revenue_region` runs after the `group by`, so it can't see the fan-out.
- Nothing reconciles revenue across models.

To list the offending rows:

```sql
select customer_id, count(*) as n, list(region order by updated_at) as regions, list(updated_at order by updated_at) as updated
from raw_customer_regions
group by customer_id
having count(*) > 1
order by customer_id;
```

## 2. Fix

Keep only the latest region per customer before joining. Base revenue on the `orders` model so it shares one definition with `daily_revenue` and `customers`. In `models/region_revenue.sql`:

```sql
with orders as (

    select * from {{ ref('orders') }}

),

regions as (

    {#-
    The regions feed keeps a row per change, so take the latest region per customer
    #}
    select customer_id, region
    from {{ ref('raw_customer_regions') }}
    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

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

After this change, `sum(revenue)` should be 1672.00.

Notes on the fix:
- If finance wants revenue attributed to the region the customer was in *at order time*, join on `updated_at <= order_date` and take the latest such row instead. The data shows the duplicates don't change region for customers with orders, so both versions give the same result today.
- Fixing the CSV alone would also work, but it would break again the next time the feed is refreshed, because the feed is a history.
- Consider moving the deduplication into a `stg_customer_regions` view so other models can't repeat this mistake.

## 3. Tests that would have caught it

**a) Grain test on the seed.** In `models/schema.yml`, add `unique` to the seed's `customer_id`:

```yaml
  - name: raw_customer_regions
    description: One row per change from the regions feed
    columns:
      - name: customer_id
        tests:
          - not_null
```

That seed is a history, so `customer_id` alone isn't unique there. Put the uniqueness test on the deduplicated relation instead, for example `stg_customer_regions.customer_id: [unique, not_null]`. If you keep the raw seed as-is, test the composite key `(customer_id, updated_at)` there with a singular test.

**b) Reconciliation test.** This catches any fan-out or dropped rows, whatever the cause. Save it as `tests/assert_region_revenue_reconciles_to_orders.sql`:

```sql
-- Revenue by region must add up to total order revenue
with region as (
    select coalesce(sum(revenue), 0) as revenue from {{ ref('region_revenue') }}
),
orders as (
    select coalesce(sum(amount), 0) as revenue from {{ ref('orders') }}
)
select region.revenue as region_revenue, orders.revenue as order_revenue
from region, orders
where abs(region.revenue - orders.revenue) > 0.01
```

Against the current build, this test fails with 1846 vs 1672. It also fails if a customer is ever missing from the regions feed, because the inner join would silently drop their revenue.
