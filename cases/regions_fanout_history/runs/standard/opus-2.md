claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 23 s | USD 0.170 | 2026-10-15 | project ad0b16b | prompt dee3d414f358

## 1. Root cause

The bug is in **`models/region_revenue.sql`**: it joins to **`raw_customer_regions`** as if that table had one row per customer. It doesn't.

- `raw_customer_regions` has **106 rows for 100 customers**. It has an `updated_at` column, so it is a region *history* feed: a customer who moved has one row per region. The seed description in `schema.yml` ("One row per customer") is wrong, and no test enforces it.
- `join regions on orders.customer_id = regions.customer_id` therefore fans out. Every payment of a customer with N region rows is counted N times, once in each region they have ever lived in.
- The numbers match this exactly:
  - Customers: 68 − 62 = 6 extra customer-region pairs, which equals the 106 − 100 = 6 extra rows. This means all the customers with a region history have placed orders.
  - Orders: the per-region counts add up to 8+20+26+11+9+21+24 = 119, against 99 real orders. So 20 orders belong to those 6 customers.
  - Revenue: 2028 − 1672 = 356 of duplicated payments for those orders.
- The tests didn't catch it. `unique(region)` and `not_null` on `region_revenue` hold even when rows are fanned out, and the seed has no uniqueness test on `customer_id`. Everything else (`orders`, `customers`, `daily_revenue`) reconciles to 1672, so the problem is limited to this one join.

You can confirm it with:
```sql
select customer_id, count(*), list(region order by updated_at), list(updated_at order by updated_at)
from raw_customer_regions group by 1 having count(*) > 1;
```

## 2. Fix

Finance wants each customer's **current** region, so keep only the latest row per customer. Do this in a staging model so every downstream model uses the deduplicated version.

`models/staging/stg_customer_regions.sql`:
```sql
with source as (

    {#-
    The regions feed is loaded as a seed, like the other raw tables.
    It keeps history: one row per customer per region change.
    #}
    select * from {{ ref('raw_customer_regions') }}

),

current_region as (

    select
        customer_id,
        region,
        updated_at

    from source

    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

)

select * from current_region
```

In `models/region_revenue.sql`, replace the `regions` CTE:
```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

After the fix, `sum(revenue)` should be 1672.00 and `sum(customers)` should be 62.

Before relying on the fix, check whether any customer has two rows with the same `updated_at`. If so, the `row_number()` pick is arbitrary, and the feed owner needs to say which row wins. Also correct the seed description to something like "One row per customer per region change".

## 3. Tests that would have caught it

In `models/staging/schema.yml`, add a grain test on the new model:
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

Add a reconciliation test, `tests/assert_region_revenue_reconciles_to_orders.sql`. It catches fan-out, and it also catches revenue silently dropped by the inner join for customers with no region row:
```sql
with region_total as (
    select sum(revenue) as revenue, sum(customers) as customers from {{ ref('region_revenue') }}
),
order_total as (
    select sum(amount) as revenue, count(distinct customer_id) as customers
    from {{ ref('orders') }}
    where amount is not null
)
select *
from region_total, order_total
where abs(region_total.revenue - order_total.revenue) > 0.01
   or region_total.customers != order_total.customers
```

Against the current data this test would fail with 2028 vs 1672 and 68 vs 62. Of the two tests, this is the one I'd keep even if the staging model changes later, because it checks the number finance actually reconciles against.
