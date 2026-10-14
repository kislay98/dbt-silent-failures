claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 18 s | USD 0.057 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

`seeds/raw_customer_regions.csv` has 102 rows for 100 customers. The build log shows `INSERT 102`, against `INSERT 100` for `raw_customers`. The extra rows are customers listed more than once, since the table carries an `updated_at` column. The seed's `schema.yml` describes it as "One row per customer", but nothing enforces that. It only has `not_null` tests, and there is no `unique` test on `customer_id`.

`models/region_revenue.sql` joins `payments → orders → regions` on `customer_id` and then runs `sum(payments.amount)`. Each payment of a duplicated customer is therefore counted once per region row, which fans out the join. The excess is 1836 − 1672 = 164, which is the payment total of the duplicated customers.

Finance's other numbers look right because `count(distinct customer_id)` and `count(distinct order_id)` are immune to fan-out. The sum of `orders` across regions is exactly 99, equal to the order count. That suggests the duplicates repeat the same region with a different `updated_at`. If they had different regions, the orders would also show up in two regions and the total would exceed 99.

I can't see the seed, so confirm with:

```sql
select customer_id, count(*), list(region), list(updated_at)
from raw_customer_regions group by 1 having count(*) > 1
```

Every test passes because none of them checks the grain of the regions input or reconciles the output to `orders`.

## 2. Fix

Add a staging model that keeps the latest row per customer, and point `region_revenue` at it.

`models/staging/stg_customer_regions.sql`:
```sql
select
    customer_id,
    region
from {{ ref('raw_customer_regions') }}
qualify row_number() over (
    partition by customer_id
    order by updated_at desc
) = 1
```

In `models/region_revenue.sql`, change the `regions` CTE to `select * from {{ ref('stg_customer_regions') }}`. The existing comment there can stay or be updated.

If the duplicates turn out to have different regions, "latest wins" is a business decision, so confirm it with the owner of the feed. Either way, the upstream feed should be fixed so it ships one row per customer.

## 3. Tests

In `models/staging/schema.yml`, add:
```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
```

To also catch the duplicates at the source, add `unique` on the seed's `customer_id`, with `config: {severity: warn}`.

The test that would have caught this directly is a reconciliation test, `tests/assert_region_revenue_reconciles_to_orders.sql`:
```sql
select r.revenue, o.amount
from (select sum(revenue) as revenue from {{ ref('region_revenue') }}) r
cross join (select sum(amount) as amount from {{ ref('orders') }}) o
where abs(r.revenue - o.amount) > 0.005
```

This test would have failed with 1836 vs 1672. It also catches the opposite problem, where customers with no region row silently drop out of the inner join.

I haven't run any of this, since I only have the files and output pasted here.
