claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 29 s | USD 0.006 | 2026-10-15 | project ad0b16b | prompt dee3d414f358

## 1. Root cause

**`jaffle_shop/models/region_revenue.sql`** joins orders to `raw_customer_regions` on `customer_id` without deduplicating the regions feed. The feed is a history, not a current snapshot. It has 106 rows for 100 customers, and the `updated_at` column shows that customers have multiple region rows. Each order therefore fans out to every region row its customer has, so its payment amount is counted once per row:

- **Customers:** 68 customers per region sums to 68, but only 62 customers have orders. The 6 extra counts are the 6 extra region rows, which are attributed to customers who have orders.
- **Revenue:** 2028.00 − 1672.00 = 356.00 of revenue is double-counted from those duplicate rows.
- **Why the tests pass:** `unique` on `region_revenue.region` holds because the `group by` makes it unique. `not_null` on `revenue` holds because the sums are non-null. Nothing tests that `region_revenue` reconciles to `orders`, or that the regions feed has one row per customer.

The build log shows a second problem. `Found 7 models, 5 seeds, 32 data tests`, but the run executes only `28 data tests`. The 4 missing tests are the `raw_customer_regions` and `raw_payment_methods` seed tests, so the seeds were excluded from the nightly `dbt build`. Those seed tests would not have caught the duplicates anyway, because the feed legitimately has several rows per customer.

Two more weaknesses contribute:

- `region_revenue` uses an inner join to regions, so any customer with orders but no region row disappears silently and the total is under-reported.
- `customers.sql` and `schema.yml` disagree (`total_order_amount` is documented but not built). This is minor and unrelated to the report.

To confirm the cause before changing anything:

```sql
select customer_id, count(*) as region_rows
from raw_customer_regions
group by 1
having count(*) > 1
order by 2 desc;
```

I expect 6 extra rows in total and the 356.00 excess to match the payments of those customers times their extra rows. I could not verify these exact figures because the seed data is not included.

## 2. Fix

Add a staging model that keeps only each customer's latest region:

**`jaffle_shop/models/staging/stg_customer_regions.sql`** (new)

```sql
with source as (

    select * from {{ ref('raw_customer_regions') }}

),

ranked as (

    select
        customer_id,
        region,
        updated_at,
        row_number() over (
            partition by customer_id
            order by updated_at desc nulls last
        ) as region_rank

    from source

)

select
    customer_id,
    region,
    updated_at

from ranked
where region_rank = 1
```

**`jaffle_shop/models/region_revenue.sql`**: point the `regions` CTE at the new model, and use a left join so that orders with no region are kept and visible:

```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

```sql
    from payments

    join orders
        on payments.order_id = orders.order_id

    left join regions
        on orders.customer_id = regions.customer_id

    group by coalesce(regions.region, 'Unknown')
```

Changing the join to a left join adds an `'Unknown'` bucket for customers with no region, so the total stays correct. Finance may prefer that bucket to be reported separately.

Do not delete rows from `raw_customer_regions`. It is the history the ingestion job loads.

Also make the nightly job include seeds (`dbt build` without a resource-type exclusion, or run `dbt seed` first). Otherwise the seed tests keep being skipped.

After the change, `sum(region_revenue.revenue)` should equal 1672.00 and the customer count should be 62.

## 3. Tests that would have caught this

Add a uniqueness test on the deduplicated model, plus a reconciliation test on the output.

**`jaffle_shop/models/staging/schema.yml`**: add

```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
      - name: region
        tests:
          - not_null
```

If someone removes the dedupe, this fails immediately. The test belongs on the staging model rather than on the raw seed, because the raw feed is supposed to have multiple rows per customer.

**`jaffle_shop/tests/assert_region_revenue_reconciles_to_orders.sql`** (new singular test):

```sql
with region_total as (

    select sum(revenue) as revenue
    from {{ ref('region_revenue') }}

),

orders_total as (

    select sum(amount) as amount
    from {{ ref('orders') }}

)

select *
from region_total
cross join orders_total
where abs(coalesce(region_total.revenue, 0) - orders_total.amount) > 0.005
```

This test fails whenever regional revenue does not match order revenue, which is the check finance performed by hand. It would have failed on the 2028.00 vs 1672.00 difference.

Also add the seed tests back into the nightly run so the existing `not_null` tests on the regions and payment-method feeds execute.
