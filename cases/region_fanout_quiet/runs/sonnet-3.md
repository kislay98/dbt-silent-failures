claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 18 s | USD 0.056 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`seeds/raw_customer_regions.csv`, consumed by `models/region_revenue.sql`.**

The log shows the seed loaded 102 rows (`INSERT 102`), but there are only 100 customers (`raw_customers` has 100). The regions feed is therefore not one row per customer, even though the seed description says it is. The extra rows are history or re-sent rows, which is what the `updated_at` column suggests. I can't see the seed rows, so the exact duplicated customers are inferred from the symptoms.

`region_revenue.sql` joins `payments → orders → regions` on `customer_id` with no dedupe. A customer with two region rows has every payment duplicated, so `sum(payments.amount)` is inflated.

- Revenue is 1836.00 against 1672.00, an excess of 164.00, which is the payment total of the duplicated customers.
- `count(distinct customer_id)` and `count(distinct order_id)` hide the fan-out, so customers and orders look right.
- Regional order counts sum to 99, the true total. If a customer had appeared under two different regions, their orders would be counted in both regions and the sum would exceed 99. The duplicate rows are therefore most likely in the same region, probably re-sent with a new `updated_at`, so only revenue is wrong.
- Every test passed because none of them checks that `customer_id` is unique in the regions seed. `unique` on `region` passes trivially after the `group by`. `not_null` on both columns passes on the duplicated rows too.

## 2. Fix

Add a staging model that keeps the latest row per customer, and point `region_revenue` at it.

`models/staging/stg_customer_regions.sql`:
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
            order by updated_at desc
        ) as row_num

    from source

)

select customer_id, region, updated_at
from ranked
where row_num = 1
```

In `region_revenue.sql`, change the `regions` CTE to `select * from {{ ref('stg_customer_regions') }}`. Also fix the comment there, since it no longer reads directly from the seed.

If two rows for a customer can share the same `updated_at`, add a deterministic tie-breaker such as `region` to the `order by`. Also check with the source owners whether any customer really changed region, because "latest wins" would then attribute all of that customer's history to the new region.

After the fix, `sum(region_revenue.revenue)` should be 1672.00, provided every customer with a payment has a region row. The inner join silently drops customers with no region, so check that too.

## 3. Tests that would have caught it

Grain test on the staged feed, in `models/staging/schema.yml`:
```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
```
Running `unique` on `raw_customer_regions.customer_id` in the seeds block would have failed on the raw data, so add it there too (use `config: severity: warn` if you want to keep the raw feed loading).

Reconciliation test, `tests/assert_region_revenue_reconciles_to_orders.sql`:
```sql
select
    r.revenue as region_revenue,
    o.revenue as orders_revenue
from (select sum(revenue) as revenue from {{ ref('region_revenue') }}) r
cross join (select sum(amount) as revenue from {{ ref('orders') }}) o
where abs(r.revenue - o.revenue) > 0.005
```
This one catches fan-out and also customers dropped by the inner join. If some orders legitimately have no region, compare against the sum of `orders.amount` for customers present in the regions table instead.

To confirm the diagnosis before changing anything:
```sql
select customer_id, count(*) n, count(distinct region) regions
from raw_customer_regions group by 1 having count(*) > 1
```
