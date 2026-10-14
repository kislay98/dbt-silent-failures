claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 23 s | USD 0.002 | 2026-10-14 | project bef6b69 | prompt 46732e565d43

## 1. Root cause

**`jaffle_shop/models/region_revenue.sql`** joins every order to *every* region row for its customer:

```sql
join regions
    on orders.customer_id = regions.customer_id
```

`raw_customer_regions` is a change history, not one row per customer. It has an `updated_at` column, and the schema description ("One row per customer from the regions feed") is wrong about the grain. A customer who moved region has two rows, so each of their orders and payments is counted once per region. Nothing selects the current row.

The numbers fit this:

| | Expected | Reported | Extra |
|---|---|---|---|
| revenue | 1672.00 | 2028.00 | +356.00 |
| orders | 99 | 119 | +20 |
| customers | 62 | 68 | +6 |

The fan-out inflates all three. The per-region `count(distinct ...)` calls can't catch it, because `customers` is distinct within a region, and a customer in two regions is counted once in each.

**Why every test passed:**
- `region_revenue.region` `unique` and `not_null` are true by construction after `group by region`.
- `not_null` on `revenue` says nothing about its value.
- `raw_customer_regions` has only `not_null` on `customer_id` and `region`. Nothing checks its grain, and nothing reconciles `region_revenue` to `orders`.
- The seed's `updated_at` column has no test.

**Side note:** `region_revenue` refs `raw_customer_regions` as a seed, but you said the ingestion job loads the raw tables. Confirm where the duplicate rows come from. If the seed CSV in `seeds/` contains the history, `dbt seed` will keep reloading it.

I worked from the artifacts you pasted and did not query the warehouse. The arithmetic is consistent with this cause, but the verification query below should confirm it.

## 2. Fix

Make `region_revenue` use each customer's current region, which is the latest `updated_at` row. Put the selection in its own model so other models can reuse it:

```sql
-- jaffle_shop/models/customer_current_region.sql
select
    customer_id,
    region,
    updated_at
from {{ ref('raw_customer_regions') }}
qualify row_number() over (
    partition by customer_id
    order by updated_at desc
) = 1
```

Then change the `regions` CTE in `region_revenue.sql`:

```sql
regions as (

    select * from {{ ref('customer_current_region') }}

),
```

Leave the rest of the model unchanged. `payments` joined to `orders` is one row per payment, and summing payments reconciles to `orders.amount`.

Add a model entry to `models/schema.yml`:

```yaml
  - name: customer_current_region
    description: One row per customer, with the region from their most recent regions feed row
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
      - name: region
        tests:
          - not_null
```

Once rebuilt, the outputs should be `sum(revenue) = 1672.00`, `sum(orders) = 99`, and `sum(customers) = 62`. If a customer who placed an order has no region row, the inner join drops their revenue. Check that before accepting the result:

```sql
select count(distinct o.customer_id)
from orders o
left join customer_current_region r using (customer_id)
where r.customer_id is null;
```

It should return 0. If it doesn't, decide what to report for unregioned customers (for example an `'UNKNOWN'` bucket) instead of letting them disappear.

## 3. Test that would have caught this

Add a singular test that fails when the report doesn't reconcile to orders. It's the check finance was doing by hand:

```sql
-- jaffle_shop/tests/assert_region_revenue_reconciles.sql
with totals as (
    select
        (select sum(revenue) from {{ ref('region_revenue') }}) as region_revenue_total,
        (select sum(orders)  from {{ ref('region_revenue') }}) as region_orders_total,
        (select sum(amount)  from {{ ref('orders') }})         as orders_total,
        (select count(*)     from {{ ref('orders') }})         as order_count
)

select *
from totals
where abs(region_revenue_total - orders_total) > 0.005
   or region_orders_total <> order_count
```

This test fails on the current data (2028 vs 1672, 119 vs 99), so it would have flagged the bug on the first build.

Also add a grain test on the seed itself, so the history is documented and any new grain problem fails loudly:

```yaml
seeds:
  - name: raw_customer_regions
    columns:
      - name: customer_id
        tests:
          - not_null
      - name: updated_at
        tests:
          - not_null
    tests:
      - dbt_utils.unique_combination_of_columns:
          arguments:
            combination_of_columns: [customer_id, updated_at]
```

This check is on `(customer_id, updated_at)`, not `customer_id` alone, because the feed legitimately has several rows per customer. Keep the seed description accurate too, e.g. "One row per customer per region change; use customer_current_region for the current region."
