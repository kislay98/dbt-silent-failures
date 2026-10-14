claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 35 s | USD 0.007 | 2026-10-15 | project ad0b16b | prompt dee3d414f358

## 1. Root cause

**`jaffle_shop/models/region_revenue.sql`** joins to `raw_customer_regions` on `customer_id`, but that table is not one row per customer. The loaded table has 106 rows for 100 customers and carries an `updated_at` column, so it is a history of region changes. Six customers have two rows, one for each region they have been in. The join fans out:

- Each of those six customers' orders and payments is joined once per region row. Their revenue is counted twice, which accounts for 2028.00 − 1672.00 = 356.00 of overstatement.
- Each of those customers is counted in both regions. The per-region customer counts sum to 68 instead of 62 (the same 6 extra rows).
- Order counts are inflated the same way: the per-region orders sum to 119 against 99 real orders.

The `unique` test on `region_revenue.region` does not catch this. It checks the output's grouping key, which is unique by construction, so the test cannot see the fan-out.

I inferred the six duplicate customers from the counts (106 − 100 = 6, 68 − 62 = 6). The sample does not include those rows, so the query in the fix section should confirm it.

Contributing causes:

- **The grain contract is wrong.** `schema.yml` says `raw_customer_regions` is "One row per customer from the regions feed", and nothing enforces it. There is no `unique` test on `customer_id` for that table. The feed changed (it now carries `updated_at` history), and the contract still describes the old shape.
- **The seed tests never ran.** The build reports 32 data tests found and 28 run. The four missing tests are the not_null tests on `raw_customer_regions` and `raw_payment_methods`. The log also has no seed step, so the nightly job appears to skip seeds, even though the project defines them and the raw tables are loaded by ingestion. Tests on this data were not running at all.
- **Inner joins drop data silently.** The `join regions` is inner, so any order whose customer has no region row disappears without a warning.

The fix below is about the duplicated region rows. The seed-tests problem and the grain contract should be fixed alongside it (see section 3).

## 2. Fix

Finance wants each customer's **current** region, so pick the latest row per customer in a staging model and point `region_revenue` at it.

**New file `jaffle_shop/models/staging/stg_customer_regions.sql`:**

```sql
with source as (

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

**`jaffle_shop/models/region_revenue.sql`:** change the regions CTE to read from the new model:

```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

The rest of the file stays the same. If you prefer to keep every paying customer in the output, change `join regions` to `left join regions` and wrap the region in `coalesce(regions.region, 'unknown')`. That is a choice for finance, so I have not made it here.

**Before merging, run these checks:**

```sql
-- confirms the six duplicates
select customer_id, count(*) from raw_customer_regions group by 1 having count(*) > 1;

-- ties on updated_at would make "current" nondeterministic
select customer_id, updated_at, count(*)
from raw_customer_regions
group by 1, 2
having count(*) > 1;
```

If the tie query returns rows, add a tiebreaker to the `order by` (for example, a sequence column from the feed) before relying on the result.

**Schema changes in `jaffle_shop/models/staging/schema.yml`:**

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

After the change, `sum(region_revenue.revenue)` should be 1672.00, the per-region customers should sum to 62, and the orders should sum to 99.

## 3. Test that would have caught this

Add a singular test at `jaffle_shop/tests/assert_region_revenue_reconciles_to_orders.sql`. It fails if the regional total drifts from the order total, which is the number finance actually reconciles to:

```sql
with totals as (

    select
        (select sum(revenue) from {{ ref('region_revenue') }}) as region_total,
        (
            select sum(o.amount)
            from {{ ref('orders') }} as o
            where exists (
                select 1
                from {{ ref('stg_customer_regions') }} as r
                where r.customer_id = o.customer_id
            )
        ) as orders_total

)

select *
from totals
where abs(region_total - orders_total) > 0.005
```

The `unique` test on `stg_customer_regions.customer_id` would also have failed on the original fan-out, if it had been pointed at a model that is supposed to be one row per customer.

To stop this from happening silently again:

- **Declare the raw tables as sources** (`sources.yml`) rather than seeds, with their tests, so they run in `dbt build` and the nightly job cannot skip them. Confirm why the nightly build excludes seeds and whether the four seed tests were ever meant to run.
- **Fix the `schema.yml` grain description** for `raw_customer_regions` to say it is a history with `updated_at`, and ask the ingestion owner whether the feed change was intentional.
- **Replace the meaningless `unique` test on `region_revenue.region`** with the reconciliation test above. A `region` value can only appear once in a group-by output, so that test verifies nothing.
