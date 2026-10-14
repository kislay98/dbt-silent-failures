claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 10 s | USD 0.048 | 2026-10-22

**1. Root cause**

`raw_customer_regions` (the seed behind `models/region_revenue.sql`) has 108 rows for 100 customers. It has an `updated_at` column, so it is a history/feed with more than one row per customer: 8 customers have two region rows, probably after moving region. The seed's only tests are `not_null` on `customer_id` and `region`, so the duplicates pass.

In `region_revenue.sql`, `left join regions on orders.customer_id = regions.customer_id` is therefore a one-to-many join. Each payment for a duplicated customer is repeated once per region row, so `sum(payments.amount)` is inflated. The overcount is 1846 − 1672 = 174.00, which should equal the payments of those 8 customers counted a second time.

The customer and order counts look right because both use `count(distinct ...)`, which hides the fan-out. Revenue is a plain `sum`, so it doesn't. A duplicated customer will also show up in two regions, so the per-region figures are wrong as well as the total.

This is inferred from the schema (`updated_at`, 108 vs 100 rows) and the arithmetic. I haven't seen the seed data, so confirm with the query below.

Confirmation:
```sql
select customer_id, count(*), min(updated_at), max(updated_at)
from raw_customer_regions group by 1 having count(*) > 1;
```

**2. Fix**

Reduce regions to one row per customer, the latest by `updated_at`, before joining. Better still, add a staging model so it's reusable.

`models/staging/stg_customer_regions.sql`:
```sql
with source as (

    select * from {{ ref('raw_customer_regions') }}

),

ranked as (

    select
        customer_id,
        region,
        row_number() over (
            partition by customer_id
            order by updated_at desc
        ) as rn

    from source

)

select customer_id, region
from ranked
where rn = 1
```

In `region_revenue.sql`, change the `regions` CTE to `select * from {{ ref('stg_customer_regions') }}`. If `updated_at` can tie for a customer, add a deterministic tiebreaker to the `order by`. Also decide with finance whether "latest region" is the right attribution for historical orders. If revenue should follow the region at order time, that needs a date-range join instead.

Afterwards `sum(revenue)` should equal 1672.00.

**3. Tests**

Grain test on the new staging model, which catches the root cause:
```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
```
(Adding `unique` on `raw_customer_regions.customer_id` would have caught it too, and would fail on today's data, which is the point.)

Reconciliation test, which catches this class of fan-out regardless of cause. `tests/assert_region_revenue_reconciles_to_orders.sql`:
```sql
select r.total as region_total, o.total as orders_total
from (select sum(revenue) as total from {{ ref('region_revenue') }}) r
cross join (select sum(amount) as total from {{ ref('orders') }}) o
where abs(r.total - o.total) > 0.005
```

Also worth noting: `region_revenue` uses a left join from payments, so payments for an order with no matching customer region land in a `NULL` region group. The `not_null` test on `region` would catch that, but it is a separate failure mode.
