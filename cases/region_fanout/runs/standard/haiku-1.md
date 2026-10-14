claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 32 s | USD 0.007 | 2026-10-15 | project ad0b16b | prompt d232260b8f43

## 1. Root cause

**`jaffle_shop/models/region_revenue.sql`** joins `raw_customer_regions` on `customer_id` without deduplicating it. **`jaffle_shop/seeds/raw_customer_regions.csv`** (the seed behind it) violates its own documented grain.

- The seed is described as "One row per customer from the regions feed," but `raw_customer_regions` has **108 rows for 100 customers**. The `updated_at` column shows the feed holds multiple snapshots per customer.
- Customers with two region rows fan out in the join. Each of their payments is counted once per region row, so `sum(payments.amount)` is inflated for those customers.
- The output confirms this. `1846.00 - 1672.00 = 174.00` of duplicated revenue, and the other columns are unaffected because they use `count(distinct ...)`:
  - `customers` sums to 62, which equals the number of customers with orders.
  - `orders` sums to 99, which equals the order count.
  - Only the `sum(payments.amount)` column is inflated.
- The customers sum to exactly 62 with no cross-region double-counting, so the duplicate rows for ordering customers must carry the same region value. The duplicates differ only in `updated_at`.

I have not seen which 8 customers are duplicated, since the sample shows only 10 random seed rows. Run this to confirm:

```sql
select customer_id, count(*) as n, count(distinct region) as regions
from raw_customer_regions
group by 1
having count(*) > 1
order by 1;
```

I expect 8 extra rows and `regions = 1` for every customer returned.

The model is not wrong to use `sum(payments.amount)`. The bug is the duplicate key. `orders`, `customers`, and `daily_revenue` are consistent with the processor figure, so they are not the problem.

## 2. Fix

The `updated_at` column says the intent is "latest region per customer." Dedupe to that in a staging model:

**`jaffle_shop/models/staging/stg_customer_regions.sql`** (new)
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

    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

)

select * from latest
```

**`jaffle_shop/models/region_revenue.sql`**
```diff
 regions as (
-    {#-
-    The regions feed is loaded as a seed, like the other raw tables
-    #}
-    select * from {{ ref('raw_customer_regions') }}
+    select customer_id, region from {{ ref('stg_customer_regions') }}
 ),
```

With one region row per customer, `sum(revenue)` should be 1672.00, which matches `sum(orders.amount)`. All 62 customers with orders have a region row, so the inner join drops nothing.

If the regions feed is supposed to be one row per customer, fix it upstream instead and keep the dedupe as a guard.

## 3. Tests that would have caught it

**a) Grain test on the feed (the one that would have failed at build time).** Add `unique` to the seed's key in **`jaffle_shop/models/schema.yml`**:

```yaml
seeds:
  - name: raw_customer_regions
    columns:
      - name: customer_id
        tests:
          - not_null
          - unique   # feed is documented as one row per customer
```

This fails on the current data, which is correct. It is the same check the `customers` model already has on `customer_id`. Once the dedupe is in place, put `unique` on `stg_customer_regions.customer_id` as well, so the staging model is covered.

**b) Reconciliation test on the revenue total.** The existing `not_null` and `unique` tests on `region_revenue` cannot see a fan-out. Add a singular test at **`jaffle_shop/tests/assert_region_revenue_reconciles_to_orders.sql`**:

```sql
with region_total as (

    select sum(revenue) as total from {{ ref('region_revenue') }}

),

orders_total as (

    select sum(amount) as total
    from {{ ref('orders') }}
    where customer_id in (select customer_id from {{ ref('stg_customer_regions') }})

)

select
    region_total.total as region_total,
    orders_total.total as orders_total

from region_total
cross join orders_total
where abs(region_total.total - orders_total.total) > 0.005
```

This returns a row, and so fails, whenever the region breakdown does not sum to the order total. It would have flagged the 174.00 gap on the first build. Finance's processor reconciliation is the external version of this check, and it should be automated in dbt.
