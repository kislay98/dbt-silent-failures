claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 30 s | USD 0.006 | 2026-10-15 | project ad0b16b | prompt d232260b8f43

## 1. Root cause

**`jaffle_shop/models/region_revenue.sql`, joining to `raw_customer_regions`, which has more than one row for some customers.**

The seed `raw_customer_regions` has 108 rows for 100 customers. The extra rows are repeated regional records, and the `updated_at` column suggests they are history snapshots. The seed's description, "One row per customer", is wrong, and nothing enforces it: its only test is `not_null` on `customer_id`.

`region_revenue` joins each payment to its order and then to the region feed on `customer_id` with no deduplication. A customer with two region rows has every one of their payments counted twice, so their revenue is inflated. The bug has this exact signature:

- `sum(revenue)` is inflated: 1846 vs 1672, a difference of 174.
- `customers` is correct because it is `count(distinct customer_id)`, so duplicates don't change it.
- `orders` is correct because it is `count(distinct order_id)`.

That matches what finance sees. The 174 excess is what the duplicated customers' payments add when they are counted once per region row. I inferred this from the numbers and haven't run the queries against the data. The diagnostic in section 3 confirms it.

The tests pass for a structural reason. `unique` on `region_revenue.region` passes trivially because the model is already grouped by region. No test checks the grain of the join or reconciles the output to `orders`.

## 2. Fix

Deduplicate the regions feed to one current row per customer in a staging model, then point `region_revenue` at it. Keep the fix in the model, not the seed, because seeds are reloaded from CSV on every run.

**New file `jaffle_shop/models/staging/stg_customer_regions.sql`:**

```sql
with source as (

    select * from {{ ref('raw_customer_regions') }}

),

current_regions as (

    select
        customer_id,
        region

    from source

    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

)

select * from current_regions
```

**`jaffle_shop/models/region_revenue.sql`:** change the regions CTE to use the staging model:

```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

Before merging, check two things:

- **Ties on `updated_at`.** If a customer has two rows with the same date, `row_number()` picks one arbitrarily. Add a tiebreaker column to the `order by` if the diagnostic below shows ties.
- **Customers with orders but no region.** The inner join drops them, so the corrected total is 1672 only if every ordering customer has a region. If it comes out lower, that gap is a separate data issue to raise with finance.

## 3. Tests that would have caught it

**a) Reconciliation test.** This catches fan-out from any join, not just this one. It goes in `jaffle_shop/tests/region_revenue_reconciles_to_orders.sql`:

```sql
-- Fails if region revenue does not equal order revenue for customers that have a region
with expected as (

    select coalesce(sum(amount), 0) as amount
    from {{ ref('orders') }}
    where customer_id in (select customer_id from {{ ref('stg_customer_regions') }})

),

actual as (

    select coalesce(sum(revenue), 0) as revenue
    from {{ ref('region_revenue') }}

)

select *
from expected
cross join actual
where abs(expected.amount - actual.revenue) > 0.005
```

On the current data this returns one row (1672 vs 1846), so the build fails.

**b) Grain test on the staging model.** In `jaffle_shop/models/staging/schema.yml`:

```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
```

Also correct the seed description in `jaffle_shop/models/schema.yml` to say the seed holds history, one row per customer per update, and that `stg_customer_regions` gives the current region.

**Diagnostic to confirm the cause:**

```sql
select customer_id, count(*) as region_rows
from raw_customer_regions
group by customer_id
having count(*) > 1
order by region_rows desc;
```

You should see about 8 extra rows across the duplicated customers, which accounts for the 108 vs 100 gap.
