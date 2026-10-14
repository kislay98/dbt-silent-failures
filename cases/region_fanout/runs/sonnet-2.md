claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 17 s | USD 0.055 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

`seeds/raw_customer_regions.csv` is not one row per customer. It has 108 rows for 100 customers, and it carries an `updated_at` column. That means it is a change history, even though the schema.yml description says "One row per customer".

`models/region_revenue.sql` joins it on `customer_id` alone:

```sql
join regions on orders.customer_id = regions.customer_id
```

Every customer with more than one row in the feed has each of their payments duplicated, once per row. `sum(payments.amount)` is inflated by the fan-out: 1846 − 1672 = 174 of extra revenue.

The counts look right because they use `count(distinct ...)`, which hides the duplication. Also, the order total across regions is exactly 99, matching `orders`. So the duplicate rows must carry the same region, with a different `updated_at`. Otherwise those orders would have appeared in two regions.

Every test passed because none of them checks the grain of the seed:
- `raw_customer_regions.customer_id` only has `not_null`, with no `unique`.
- `unique` on `region_revenue.region` is trivially true after a `group by`.

I can't see the seed data, so the row-count explanation is inferred from the 108 vs 100 rows and the numbers above. To confirm:

```sql
select customer_id, count(*), min(region), max(region), min(updated_at), max(updated_at)
from raw_customer_regions group by 1 having count(*) > 1;
```

## 2. Fix

Reduce the feed to the latest row per customer before joining. In `region_revenue.sql`:

```sql
regions as (

    {#-
    The regions feed is a change history (one row per customer per updated_at),
    so keep only the current region for each customer
    #}
    select customer_id, region
    from {{ ref('raw_customer_regions') }}
    qualify row_number() over (
        partition by customer_id
        order by updated_at desc
    ) = 1

),
```

If two rows for a customer share the same `updated_at`, add a deterministic tie-breaker to the `order by`. After the fix, `region_revenue` should sum to 1672.00.

If the history was never meant to be there, de-duplicate the feed upstream instead. Either way, keep the `unique` test below.

## 3. Tests

Enforce the grain in `schema.yml` (this alone would have failed the build):

```yaml
seeds:
  - name: raw_customer_regions
    columns:
      - name: customer_id
        tests:
          - not_null
          - unique
```

Note that this fails on the raw feed as it is today. Apply it to a deduplicated staging model, such as a new `stg_customer_regions`, if you want to keep the history in the seed.

Add a reconciliation test, `tests/assert_region_revenue_reconciles_to_orders.sql`. It catches any future fan-out or dropped rows, including customers with no region:

```sql
select r.revenue, o.amount
from (select sum(revenue) as revenue from {{ ref('region_revenue') }}) r
cross join (select sum(amount) as amount from {{ ref('orders') }}) o
where abs(r.revenue - o.amount) > 0.005
```
