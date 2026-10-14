claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 35 s | USD 0.007 | 2026-10-15 | project ad0b16b | prompt dee3d414f358

## 1. Root cause

**`jaffle_shop/models/region_revenue.sql` joins to `raw_customer_regions` without reducing it to one row per customer.** The seed is a history feed, not a current-state table. It has an `updated_at` column and 106 rows for 100 customers, so 6 customers have two region rows. Each of those customers' orders and payments are duplicated, once per region.

The totals confirm the fan-out:

| Metric | Reported | Expected | Extra |
|---|---|---|---|
| `revenue` | 2028.00 | 1672.00 | 356.00 |
| `orders` | 119 | 99 | 20 |
| `customers` | 68 | 62 | 6 |
| rows in `raw_customer_regions` | 106 | 100 customers | 6 |

The 6 extra region rows account exactly for the 6 extra customer-region pairs. The 20 extra orders and 356.00 extra revenue are the activity of those customers, counted in both of their regions.

The data is not wrong. The model's grain is wrong. The seed's description, "One row per customer from the regions feed," is false for the data as loaded, and nothing enforces it.

**Why the tests passed:**
- `region_revenue.region` is `unique`, but that holds after `group by region`, so the fan-out can't break it.
- `customer_id` in `raw_customer_regions` only has `not_null`, not `unique`.
- No test compares the output total to `orders`, so the 2028 vs 1672 mismatch was never checked.

**A second gap: four seed tests never ran.** The log shows "Found … 5 seeds, 32 data tests" but only 28 data tests ran, and no seed nodes appear. The four missing tests are the seed column tests: `not_null` on `raw_customer_regions.customer_id` and `region`, and on `raw_payment_methods.payment_method` and `fee_rate`. The log doesn't show the command, so I can't say why they were excluded. It's likely the seeds are loaded by the ingestion job and the build was run with a seed exclusion, which also dropped their tests. "Every test passing" therefore covers less than it appears to.

## 2. Fix

Reduce the regions feed to the current region per customer in a staging model, then have `region_revenue` use it.

**New file: `jaffle_shop/models/staging/stg_customer_regions.sql`**

```sql
with source as (

    {#-
    One row per customer per region change. Keep only the latest row per customer
    so each customer is counted once, in their current region
    #}
    select * from {{ ref('raw_customer_regions') }}

),

current_regions as (

    select
        customer_id,
        region,
        updated_at

    from source

    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

)

select * from current_regions
```

**Edit: `jaffle_shop/models/region_revenue.sql`**, replace the `regions` CTE:

```sql
regions as (

    {#-
    Current region per customer, see stg_customer_regions
    #}
    select * from {{ ref('stg_customer_regions') }}

),
```

**Edit: `jaffle_shop/models/staging/schema.yml`**, add:

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

**Edit: `jaffle_shop/models/schema.yml`**, correct the seed description so it states the grain:

```yaml
  - name: raw_customer_regions
    description: Region history per customer from the regions feed, one row per region change (customer_id, region, updated_at). Use stg_customer_regions for the current region.
```

**Also fix the data-side expectation with the ingestion team.** If the feed will only ever be history, that's fine. The model handles it. If they can deliver a current-state table instead, that's simpler, but it's not required.

**Expected output after the fix:** `sum(customers) = 62`, `sum(orders) = 99`, `sum(revenue) = 1672.00`. This follows from the numbers above, but I haven't run it. Verify with `dbt build --select +region_revenue` and the query from the output sample.

**Ties:** if two rows for one customer share the same `updated_at` date, the `unique` test on `stg_customer_regions.customer_id` will fail. That's intended: finance needs an explicit tie-break rule, and the test is what forces one.

## 3. Test that would have caught this

Add a singular test that checks the reported revenue against the processor-reconciled orders total. This is the check finance actually performed.

**New file: `jaffle_shop/tests/assert_region_revenue_reconciles_to_orders.sql`**

```sql
-- Fails if region_revenue does not add up to orders, which reconciles to the processor settlement.
-- Catches fan-out from joins and customers with no region row.
select
    region_total,
    orders_total
from (
    select
        (select sum(revenue) from {{ ref('region_revenue') }}) as region_total,
        (select sum(amount) from {{ ref('orders') }}) as orders_total
) totals
where abs(region_total - orders_total) > 0.005
```

Against the original models this returns one row (2028.00 vs 1672.00), so `dbt test` fails. After the fix it returns no rows. Note that it also fails if any ordering customer is missing from the regions feed, which is the coverage gap behind the second symptom.

The `unique` test on `stg_customer_regions.customer_id` (section 2) guards the grain at its source.

Finally, run the four seed tests. Remove whatever exclusion dropped them, or run `dbt test --select resource_type:seed`, so the `not_null` checks on the raw feeds actually execute.

## Other things I noticed

- `models/schema.yml` documents `customers.total_order_amount`, which the model doesn't produce. The column is `customer_lifetime_value`. This is documentation drift and doesn't affect the numbers.
- `daily_revenue` ties out to `orders` by month (1672.00 total), so it isn't affected.
