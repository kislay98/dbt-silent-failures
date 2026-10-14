claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 25 s | USD 0.119 | 2026-10-14 | project f769fdf | prompt 9ea1d7d8a74d

## 1. Root cause

**The regions seed has more than one row per customer, and `models/region_revenue.sql` joins to it without deduplicating. Each payment from an affected customer is counted once per region row.**

The evidence:

- **Row counts don't match.** `raw_customer_regions` loaded **108 rows**, but `raw_customers` has **100**. The seed is documented as "one row per customer," yet it carries an `updated_at` column (see the `information_schema` output). It is a change-history feed: when a customer's region record is re-sent, a new row is appended.
- **The join multiplies rows.** `payments → orders → regions` is many-to-one-to-many. If a customer has 2 region rows, each of their payments shows up twice in the joined set.
- **The `count(distinct ...)` columns hide the problem.** `customers` and `orders` use `count(distinct ...)`, so duplicate rows collapse and finance sees correct counts. `revenue` uses a plain `sum(payments.amount)`, so it is inflated by **174.00** (1846 − 1672).
- **The duplicates stay within one region.** `sum(orders)` across regions is exactly 99. That means no order was counted in two different regions, so every duplicate row for a customer with orders repeats the *same* region with a newer `updated_at`. That is why the per-region counts look right and only the revenue is wrong.
- **No test checks for this.** `models/schema.yml` tests `raw_customer_regions.customer_id` for `not_null` only, not `unique`. Every other test is on a grain the fan-out doesn't affect, so the build passes.

To confirm against the warehouse:

```sql
select customer_id, count(*) as n, count(distinct region) as regions
from raw_customer_regions
group by customer_id
having count(*) > 1;
```

## 2. Fix

Deduplicate to the latest region per customer in a staging model, and point `region_revenue` at it.

**`models/staging/stg_customer_regions.sql`** (new)
```sql
with source as (

    {#-
    Normally we would select from the table here, but we are using seeds to load
    our data in this project
    #}
    select * from {{ ref('raw_customer_regions') }}

),

latest as (

    -- the regions feed appends a new row whenever a customer's region is re-sent,
    -- so keep only the most recent row per customer
    select
        customer_id,
        region,
        updated_at

    from source

    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

)

select * from latest
```

**`models/region_revenue.sql`**: change the `regions` CTE:
```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

After this change, `sum(region_revenue.revenue)` should equal 1672.00.

Two caveats:
- **This reports by current region.** If finance wants revenue attributed to the region the customer was in *when the order was placed*, you'd need a point-in-time join instead: an `updated_at <= order_date` range join, or a dbt snapshot with `dbt_valid_from`/`dbt_valid_to`.
- **Orders without a region would be dropped.** The inner join to `regions` silently drops any order whose customer has no region row. None are dropped today (99 = 99), but a `left join` with `coalesce(regions.region, 'Unknown')` would make that failure visible instead of silent.

## 3. Tests that would have caught it

**Enforce the grain.** In `models/staging/schema.yml`:
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
Optionally, also add `unique` to `raw_customer_regions.customer_id` with `config: {severity: warn}`, so you learn when the feed starts sending history.

**Reconcile totals against the source of truth.** This catches any fan-out or dropped rows, whatever the cause. Add `tests/assert_region_revenue_reconciles_to_orders.sql`:
```sql
-- region revenue must reconcile to order revenue (which finance ties to settlement)
with region_total as (
    select sum(revenue) as revenue from {{ ref('region_revenue') }}
),

orders_total as (
    select sum(amount) as revenue from {{ ref('orders') }}
)

select *
from region_total
cross join orders_total
where abs(region_total.revenue - orders_total.revenue) > 0.01
```

This singular test would have failed the current build (1846 vs 1672). The `unique` test pinpoints the cause.
