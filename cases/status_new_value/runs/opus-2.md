claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 30 s | USD 0.128 | 2026-10-14 | project f769fdf | prompt 962ccbf68360

# Root cause: a new `delivered` status that the models don't count as completed

## The numbers

| | Completed in the models | Delivered | Sum | Admin |
|---|---|---|---|---|
| March | 143.00 | 136.00 | **279.00** | 279.00 |
| Since January | 424 + 400 + 143 = 967.00 | 136.00 | **1103.00** | 1103.00 |

The gap is exactly the `delivered` orders. Total revenue matches in every month because no money is missing; it is only filed under the wrong status.

## 1. Root cause

- **The data:** Starting in March 2018, `raw_orders.status` (loaded by the ingestion job, not by dbt) contains a new value, `delivered`. The shop admin counts it as completed. January and February have no `delivered` rows. March has both `completed` and `delivered`, which suggests the source system switched from one to the other partway through the month. You can confirm this with:
  ```sql
  select status, min(ordered_at), max(ordered_at), count(*)
  from raw_orders where status in ('completed', 'delivered') group by 1;
  ```
- **`jaffle_shop/models/customers.sql`:** `completed_amount` uses `case when orders.status = 'completed' ...`. That drops `delivered` orders from `completed_lifetime_value`, which gives 967 instead of 1103. Anything that reads `orders.status = 'completed'` downstream has the same problem, which gives 143 instead of 279 for March.
- **Why the tests passed:** `'delivered'` was added to the `accepted_values` lists in both `models/staging/schema.yml` (on `stg_orders.status`) and `models/schema.yml` (on `orders.status`). Someone widened the test so the new value would pass, but never updated the logic that decides what counts as completed. The one test that would have flagged the new status was turned off in practice.

## 2. Fix

Normalize the status once, in staging, so every downstream model uses the same definition. In `models/staging/stg_orders.sql`:

```sql
renamed as (

    select
        id as order_id,
        user_id as customer_id,
        ordered_at,
        cast(ordered_at as date) as order_date,
        -- the shop platform started emitting 'delivered' for completed orders in March 2018
        case when status = 'delivered' then 'completed' else status end as status,
        status as source_status

    from source

)
```

Then remove `'delivered'` from both `accepted_values` lists, so it is only accepted after the mapping. Before shipping, confirm with finance or the shop team that `delivered` really means completed and is not a separate step before it. The admin totals strongly suggest it means completed.

After the change, run `dbt build`. `daily_revenue` is incremental, but it only aggregates `amount` by date and doesn't use status, so it needs no full refresh. `customers` and `orders` are tables and rebuild completely.

## 3. Tests that would have caught it

**a) Keep `accepted_values` strict and connected to the business logic.** On `stg_orders`, test the raw values against an explicit list of statuses the models know how to handle:

```yaml
      - name: source_status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'delivered', 'return_pending', 'returned']
              config:
                severity: error
      - name: status
        tests:
          - accepted_values:
              arguments:
                values: ['placed', 'shipped', 'completed', 'return_pending', 'returned']
```

The second test is the one that would have caught this: an unmapped new source status fails the build instead of quietly dropping out of completed revenue.

**b) A reconciliation test**, `tests/assert_completed_revenue_reconciles.sql`. It checks that the customer-level completed value matches the order-level one, so the two models can't drift apart in how they define completed:

```sql
with c as (
    select round(sum(completed_lifetime_value), 2) as v from {{ ref('customers') }}
),
o as (
    select round(sum(amount), 2) as v from {{ ref('orders') }} where status = 'completed'
)
select c.v as customers_completed, o.v as orders_completed
from c, o
where c.v <> o.v
```

The strongest guard is a check against the source of truth: a source freshness or reconciliation test that compares monthly completed revenue with an export of the admin's figures, if one is available.

## A separate issue I noticed

`raw_customer_regions` has an `updated_at` column. That suggests the regions feed can hold several rows per customer, but its schema only tests `not_null` and not `unique` on `customer_id`. If a customer appears twice, `region_revenue` would double-count their payments. This isn't part of the reported problem, but adding `unique` on `customer_id`, or deduplicating to the latest `updated_at`, is worth doing.
