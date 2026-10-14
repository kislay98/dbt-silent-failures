claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 22 s | USD 0.004 | 2026-10-14 | project bef6b69 | prompt 46732e565d43

**Caveat:** I haven't queried the warehouse, so the root cause below is inferred from the files, log, and output. The row counts fit the explanation closely, and the first step under Fix confirms it.

## 1. Root cause

**File:** `jaffle_shop/models/region_revenue.sql`, joined against `raw_customer_regions` (seed `raw_customer_regions`, documented in `jaffle_shop/models/schema.yml`).

The regions feed is not one row per customer. Its schema has an `updated_at` column, so it keeps history: a customer who moved has more than one row. The model joins `orders` to `regions` on `customer_id` with no filter for the current row. Each order and payment is then duplicated once per region row its customer has, and the `sum(revenue)` and `sum(orders)` columns fan out.

The row counts match this:

- Customers: 68 in the report vs 62 real. The 6 extra rows are customer–region pairs. A customer with two region rows is counted in both regions.
- Orders: 119 in the report vs 99 real. The extra 20 are the same order counted again under each of a customer's regions.
- Revenue: 2028 vs 1672. Payments are multiplied by the same fan-out, so the overstatement is about 21%, consistent with the order inflation.

The tests pass because of how they are written:

- `unique` on `region_revenue.region` is true by construction after `group by region`. It cannot detect duplicated rows within a region.
- The seed tests check only `not_null` on `customer_id` and `region`. Nothing checks uniqueness of `customer_id`, even though the seed description says "one row per customer", which the data contradicts.
- No test compares the report total to `orders` or payments.

The seed description and the model comment also assume a single row per customer, which is wrong for a history table.

## 2. Fix

**a) Confirm the duplication** (should return one row per customer with more than one region):

```sql
select customer_id, count(*) as n
from raw_customer_regions
group by customer_id
having count(*) > 1;
```

**b) Add a model that resolves each customer to their current region.** Create `jaffle_shop/models/customer_regions.sql`:

```sql
with ranked as (

    select
        customer_id,
        region,
        updated_at,
        row_number() over (
            partition by customer_id
            order by updated_at desc
        ) as row_num

    from {{ ref('raw_customer_regions') }}

)

select
    customer_id,
    region

from ranked
where row_num = 1
```

If two rows can share the same `updated_at`, add a deterministic tiebreaker to the `order by`, such as an ingestion sequence column if the feed has one.

**c) Point `region_revenue` at it.** In `jaffle_shop/models/region_revenue.sql`, replace the `regions` CTE body with `select * from {{ ref('customer_regions') }}`. Leave the rest of the model as it is. The `count(distinct ...)` aggregates are correct once each customer maps to one region.

**d) Fix the docs and tests** in `jaffle_shop/models/schema.yml`:

- Add a `customer_regions` model entry with `customer_id` tested for `unique` and `not_null`.
- Correct the seed description if the feed keeps history. Document `updated_at` as "the date this region assignment took effect".

**e) Check ownership of the feed.** The log has no seed step, and you said the raw tables are loaded by the ingestion job. If `raw_customer_regions` is also a dbt seed, a `dbt seed` run would overwrite the ingested table, so make sure only one of them owns it.

After the change, the totals should be 62 customers, 99 orders, and 1672.00 revenue. Rebuild `region_revenue` and check that.

## 3. A test that would have caught this

Add a reconciliation test that compares the regional total to the order total. Create `jaffle_shop/tests/region_revenue_reconciles_to_orders.sql`:

```sql
with totals as (

    select
        (select sum(amount) from {{ ref('orders') }}) as order_total,
        (select sum(revenue) from {{ ref('region_revenue') }}) as region_total

)

select *
from totals
where abs(order_total - region_total) > 0.005
```

This test fails on the current data (2028 vs 1672). It also catches the second problem in this pipeline. Because `region_revenue` uses an inner join, any customer missing from the regions feed is dropped silently, and the test flags that as well.

Also add a uniqueness test on the seed's `customer_id` (or the `customer_regions` model, as in step 2d), since that is the constraint the model actually depends on.
