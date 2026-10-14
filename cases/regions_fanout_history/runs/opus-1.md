claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 35 s | USD 0.121 | 2026-10-14 | project f769fdf | prompt dad92f6afadb

# Root cause: `raw_customer_regions` has more than one row per customer

## 1. Root cause

**Files:** `models/region_revenue.sql` and the `raw_customer_regions` contract in `models/schema.yml`.

`raw_customer_regions` is documented as "One row per customer". Its columns say otherwise: it has an `updated_at DATE` column. That makes it a change history, and a customer who moved region has one row per region they have been in.

`region_revenue.sql` joins straight to that table:

```sql
join regions
    on orders.customer_id = regions.customer_id
```

This fans out:

- Every payment for a multi-row customer is counted once per region row. Revenue is overstated by 2028 − 1672 = **356**, and order count by 119 − 99 = **20**.
- The same customer is counted in each historical region they appear in. Customers add up to 68 − 62 = **6** extra customer/region pairs.
- Finance wants the **current** region only, but the model never filters on `updated_at`.

**Why the tests passed:**
- No test anywhere asserts `customer_id` is unique in `raw_customer_regions`. The seed only has `not_null` tests.
- Even those seed tests didn't run. The log shows "29 data tests" found but only 27 executed (34 = 7 models + 27 tests), because the nightly build doesn't select the seed or its tests.
- `unique_region_revenue_region` passes because the output is still one row per region. It's the totals that are wrong.

To confirm on the warehouse:

```sql
-- customers with more than one region row
select customer_id, count(*) as n, count(distinct region) as regions,
       list(region order by updated_at) as history
from raw_customer_regions
group by customer_id
having count(*) > 1;

-- inflation caused by the fan-out (expected: 356)
select sum(p.amount * (r.n - 1))
from stg_payments p
join stg_orders o using (order_id)
join (select customer_id, count(*) n from raw_customer_regions group by 1) r
  on o.customer_id = r.customer_id;
```

## 2. Fix

Add a staging model that keeps only each customer's latest region, and point `region_revenue` at it.

**`models/staging/stg_customer_regions.sql`** (new):

```sql
with source as (

    select * from {{ ref('raw_customer_regions') }}

),

current_region as (

    select
        customer_id,
        region,
        updated_at

    from source

    -- the feed keeps one row per region change; finance reports on the current region
    qualify row_number() over (
        partition by customer_id
        order by updated_at desc, region
    ) = 1

)

select * from current_region
```

The `region` tie-breaker makes the result deterministic if two rows share an `updated_at`. Any such ties should still be raised with the feed owner.

**`models/region_revenue.sql`**, in the `regions` CTE:

```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

Also check the inverse problem: the inner join silently drops orders from customers with **no** region row. If the reconciliation test below fails after the fix, switch to a `left join` and use `coalesce(regions.region, 'UNKNOWN')`.

## 3. Tests that would have caught it

**`models/staging/schema.yml`**:

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

**`models/schema.yml`**: fix the seed description and test the grain it actually has:

```yaml
seeds:
  - name: raw_customer_regions
    description: One row per customer per region change; latest updated_at is the current region
    tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: ['customer_id', 'updated_at']
```

**`tests/assert_region_revenue_reconciles_to_orders.sql`**: the test that catches any fan-out or dropped rows, whatever the cause:

```sql
with r as (
    select sum(revenue) as revenue, sum(orders) as orders, sum(customers) as customers
    from {{ ref('region_revenue') }}
),
o as (
    select sum(amount) as revenue, count(*) as orders, count(distinct customer_id) as customers
    from {{ ref('orders') }}
    where amount is not null
)
select *
from r cross join o
where abs(r.revenue - o.revenue) > 0.01
   or r.orders != o.orders
   or r.customers != o.customers
```

On today's data this returns 2028 vs 1672, 119 vs 99, and 68 vs 62, so it fails.

Also fix the nightly build selection so tests on `raw_customer_regions` actually run. For example, use `dbt build --exclude resource_type:seed` and then `dbt test --select raw_customer_regions`, or define the raw tables as `sources` with tests. The two seed tests found but not executed in the log are how this slipped through.
