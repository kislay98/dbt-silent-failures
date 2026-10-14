claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 17 s | USD 0.086 | 2026-10-15 | project ad0b16b | prompt d232260b8f43

## 1. Root cause

**`seeds/raw_customer_regions` is not one row per customer, and `models/region_revenue.sql` joins it as if it were.**

- The seed has 108 rows for 100 customers (the `raw_customer_regions` row count in the log and output sample). It also carries an `updated_at` column, so it looks like a change-history feed.
- Its schema.yml description says "One row per customer", but only `not_null` is tested on it, never `unique`.
- In `region_revenue.sql`, `join regions on orders.customer_id = regions.customer_id` is one-to-many. Every payment for a customer with 2 region rows is emitted twice, so `sum(payments.amount)` double-counts. That is the 1846 − 1672 = 174 excess.
- `customers` and `orders` look right because they use `count(distinct ...)`, which hides the fan-out. The totals are consistent with this: orders sum to 99 and customers sum to 62 across regions, the same as the order table and the customers with orders. So no order lands in two regions.
- I can't see the seed rows. Since no order appears in two regions, the duplicated customers' extra rows must carry the same region with a different `updated_at` (or belong to customers without orders, but 174 of excess revenue rules that out). Run the query below to confirm.
- Every test passes because none of them checks uniqueness of the join key on the dimension side, or reconciles `region_revenue` back to `orders`.

Confirm with:
```sql
select customer_id, count(*), count(distinct region), min(updated_at), max(updated_at)
from raw_customer_regions group by 1 having count(*) > 1;
```

## 2. Fix

Add a staging model that keeps the latest row per customer, and point `region_revenue` at it.

`models/staging/stg_customer_regions.sql`
```sql
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

`region_revenue.sql`: change the `regions` CTE to:
```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

If any duplicate pairs have different regions, "latest wins" is a business decision. If so, confirm it with finance. Ties on `updated_at` would also need a tiebreaker in the `order by`.

After the fix, `sum(revenue)` should be 1672.00. The per-region customer and order counts should be unchanged.

## 3. Tests that would have caught it

Uniqueness on the dimension key, in `models/staging/schema.yml`. This would have failed on the raw seed with 108 rows:
```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
```
Also add `unique` to `raw_customer_regions.customer_id` in `models/schema.yml`, but expect it to fail until the feed is deduped or the seed is cleaned. If you want the raw feed to be allowed duplicates, test only the staging model.

A reconciliation test, `tests/assert_region_revenue_matches_orders.sql`, which catches any fan-out regardless of cause:
```sql
select r.total as region_total, o.total as orders_total
from (select sum(revenue) as total from {{ ref('region_revenue') }}) r
cross join (select sum(amount) as total from {{ ref('orders') }}) o
where abs(r.total - o.total) > 0.005
```
This test returns a row, and so fails, on the current project (1846 vs 1672). It would pass after the fix.
