claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 25 s | USD 0.004 | 2026-10-14 | project bef6b69 | prompt 46732e565d43

## 1. Root cause

`region_revenue` joins payments to orders to `raw_customer_regions` on `customer_id`, and `raw_customer_regions` has more than one row per customer. The seed description says "one row per customer," and `schema.yml` only tests `customer_id` for `not_null`, so nothing enforces that. The table has an `updated_at` column, which suggests it keeps region history. Each extra region row duplicates that customer's orders and payments in the join.

The numbers fit this:
- **Customers:** 68 summed across regions vs 62 real customers. Six customers appear under two region rows, so `count(distinct customer_id)` counts them in both regions.
- **Revenue:** 2028.00 vs 1672.00. Each payment is multiplied by the number of region rows for its customer, so the total is inflated.
- **Orders:** 119 vs 99 in the orders table, for the same reason.

The model should use each customer's **current** region, which is the latest row by `updated_at`. The model doesn't do that, so it fans out instead.

Files involved:
- `jaffle_shop/models/region_revenue.sql`: joins the raw, un-deduplicated regions feed (`ref('raw_customer_regions')`).
- `jaffle_shop/models/schema.yml`: `raw_customer_regions` documents "one row per customer" but has no uniqueness test, and `updated_at` is undocumented.

The existing tests passed for these reasons:
- `unique` on `region_revenue.region` holds by construction, because the model is grouped by region.
- `not_null` cannot detect duplicated rows.
- No test compares `region_revenue` totals to `orders` or `stg_payments`.

I inferred this from the sample output and the column list, not from running queries. To confirm:

```sql
select customer_id, count(*) as n, list(region) as regions
from raw_customer_regions
group by customer_id
having count(*) > 1;
```

I expect six rows.

## 2. Fix

Collapse the regions feed to one current row per customer in a staging model, and have `region_revenue` read from it.

**New file `jaffle_shop/models/staging/stg_customer_regions.sql`:**

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

select
    customer_id,
    region,
    updated_at

from ranked
where row_num = 1
```

**`jaffle_shop/models/region_revenue.sql`:** change the `regions` CTE:

```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

**`jaffle_shop/models/schema.yml`:**
- Add `updated_at` to the `raw_customer_regions` column list and correct its description, since the feed is history rather than one row per customer.

Things to check:
- If two rows for a customer share the same `updated_at`, the pick is arbitrary. Add a tie-breaker such as the feed's sequence ID if one exists. I can't tell from the schema.
- The raw table is loaded by the ingestion job, so confirm with its owner that `updated_at` is the change timestamp.
- `region_revenue` is a table, so the next `dbt build` rebuilds it. `daily_revenue` doesn't join regions, so it is unaffected.

## 3. Tests that would have caught this

**a) Grain tests on the model that feeds the join** (`staging/schema.yml`):

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

Without the staging model, `unique` on `raw_customer_regions.customer_id` would have failed on the first run. That failure is the signal you want. The staging model exists to make that test pass legitimately.

**b) A reconciliation test** (`tests/assert_region_revenue_reconciles.sql`):

```sql
select 1
from (
    select
        (select sum(revenue) from {{ ref('region_revenue') }}) as region_total,
        (select sum(amount) from {{ ref('orders') }}) as order_total
) totals
where abs(region_total - order_total) > 0.005
```

This is the test that matches what finance checks. It would have failed on 2028.00 vs 1672.00. It will also fail if any customer with orders has no region row, because that revenue is dropped by the inner join. That is a real gap that finance should see, so treat a failure as information rather than noise.
