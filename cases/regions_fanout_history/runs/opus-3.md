claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 35 s | USD 0.138 | 2026-10-14 | project f769fdf | prompt dad92f6afadb

## 1. Root cause

**File:** `jaffle_shop/models/region_revenue.sql`, the `regions` CTE. The underlying data problem is in `raw_customer_regions`.

`raw_customer_regions` is documented as "one row per customer". The warehouse schema shows it also has an `updated_at DATE` column. That column means the ingestion feed is a change history: a customer gets a new row each time their region changes, and possibly also when the feed re-delivers a row. `region_revenue` joins this table directly on `customer_id`, so the join fans out:

- **Revenue is inflated.** Each payment from a customer with N region rows is summed N times. 2028 − 1672 = 356 of duplicated revenue.
- **Customer counts are inflated.** A customer who moved shows up in both their old and new region. 68 − 62 = 6 extra customer-region pairs.
- **Order counts are inflated** the same way. The regional sum is 119 against 99 actual orders.
- **The report uses historical regions, not the current one.** Finance wants each customer's current region, but old regions still receive revenue.
- `count(distinct …)` hides duplicates *within* a single region. Revenue still double-counts there, so revenue drifts further than the counts do.

**Why every test passed:**
- Nothing asserts that `customer_id` is unique in the regions feed.
- The two tests that do exist on `raw_customer_regions` (`not_null`) never ran. The log says "Found … 29 data tests" but only 27 executed. The build covered 34 nodes = 7 models + 27 tests, with no seeds. The raw tables come from ingestion, so seeds and their tests are excluded from the nightly run. `ref('raw_customer_regions')` still resolves to the ingested table, so nothing errors.
- `unique_region_revenue_region` still passes after the fan-out, because the model is grouped by region.

To confirm against the data (I don't have the rows):

```sql
select customer_id, count(*) as rows, count(distinct region) as regions
from raw_customer_regions
group by customer_id
having count(*) > 1;
```

## 2. Fix

Reduce the feed to one current row per customer before joining. Do it in a staging model so other models can't repeat the mistake. Also declare the table as a source, since it is ingested and not seeded.

`models/staging/sources.yml`:
```yaml
version: 2

sources:
  - name: raw
    schema: main
    tables:
      - name: raw_customer_regions
        description: Regions feed from ingestion; one row per customer per region change
        columns:
          - name: customer_id
            tests:
              - not_null
          - name: region
            tests:
              - not_null
```
Then delete the `seeds:` block for `raw_customer_regions` from `models/schema.yml`, and remove the seed CSV if one exists.

`models/staging/stg_customer_regions.sql`:
```sql
with source as (

    select * from {{ source('raw', 'raw_customer_regions') }}

),

current_region as (

    -- the feed keeps a row per region change, so take the latest one per customer
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
The `region` tiebreaker only keeps the result deterministic if two rows share an `updated_at`. If that happens, raise it with the feed owner.

`models/region_revenue.sql`: replace the `regions` CTE, and use a left join so customers without a region aren't silently dropped from the total:
```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),

final as (

    select
        coalesce(regions.region, 'UNKNOWN') as region,
        count(distinct orders.customer_id) as customers,
        count(distinct orders.order_id) as orders,
        sum(payments.amount) as revenue

    from payments

    join orders
        on payments.order_id = orders.order_id

    left join regions
        on orders.customer_id = regions.customer_id

    group by 1

)
```
After this change, the regional totals should be 1672.00 revenue, 99 orders and 62 customers. Each customer now maps to exactly one region, so customers sum without overlap.

## 3. Tests that would have caught it

**Grain test on the staging model**, added to `models/staging/schema.yml`. This one runs in `dbt build`:
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

**Reconciliation test.** This catches any future fan-out or drop, whatever causes it. `tests/assert_region_revenue_reconciles_to_orders.sql`:
```sql
with regional as (

    select
        sum(revenue) as regional_revenue,
        sum(orders) as regional_orders,
        sum(customers) as regional_customers
    from {{ ref('region_revenue') }}

),

actual as (

    select
        sum(amount) as actual_revenue,
        count(*) as actual_orders,
        count(distinct customer_id) as actual_customers
    from {{ ref('orders') }}

)

select *
from regional
cross join actual
where abs(regional_revenue - actual_revenue) > 0.01
   or regional_orders <> actual_orders
   or regional_customers <> actual_customers
```
On the current data, this test returns one row (2028/119/68 vs 1672/99/62) and fails the build.

Finally, moving the table to `source()` means its tests now run in the nightly `dbt build`. Before, those tests only existed on a seed node that the build skipped.
