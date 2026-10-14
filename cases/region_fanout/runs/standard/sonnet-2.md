claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 17 s | USD 0.086 | 2026-10-15 | project ad0b16b | prompt d232260b8f43

## 1. Root cause

`seeds/raw_customer_regions.csv` has 108 rows for 100 customers. It is a history feed with an `updated_at` column, so some customers appear more than once. `models/region_revenue.sql` joins it directly on `customer_id`:

```sql
join regions on orders.customer_id = regions.customer_id
```

Each payment for a customer with N region rows is therefore counted N times. That is a fan-out, and it inflates the revenue by 174.00 (1846 − 1672).

Everything else looks right because:
- `count(distinct customer_id)` and `count(distinct order_id)` are immune to fan-out. Finance's counts matched for that reason.
- The per-region customer counts sum to 62, which equals the number of customers with orders. So the duplicate rows for a customer carry the same region, not conflicting ones. I inferred this from the output and haven't queried the seed directly.
- `orders.sql` and `customers.sql` don't touch the regions seed. Their totals (1672.00) match settlement, and `daily_revenue` is correct too.
- The only tests on the seed are `not_null`. Nothing checks that `customer_id` is unique, and no test reconciles the report to `orders`.

To confirm:
```sql
select customer_id, count(*), count(distinct region)
from raw_customer_regions group by 1 having count(*) > 1;
```

## 2. Fix

Add `models/staging/stg_customer_regions.sql`, which keeps the latest row per customer:

```sql
with source as (

    select * from {{ ref('raw_customer_regions') }}

),

ranked as (

    select
        customer_id,
        region,
        updated_at,
        row_number() over (
            partition by customer_id
            order by updated_at desc, region
        ) as rn

    from source

)

select customer_id, region, updated_at
from ranked
where rn = 1
```

The `region` tiebreaker keeps the result deterministic if two rows share an `updated_at`.

In `region_revenue.sql`, change the `regions` CTE to `select * from {{ ref('stg_customer_regions') }}`. A cheaper alternative is to compute revenue from `ref('orders')` (`amount`) instead of re-joining payments. Deduping the regions feed is still needed so the order and customer counts don't fan out.

If the duplicates turn out to be exact repeats with different regions, finance needs to decide which region wins. "Latest `updated_at`" is the usual rule, but that is a business call.

## 3. Tests

Uniqueness on the staged model, in `models/staging/schema.yml`:

```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
```

A `unique` test on `raw_customer_regions.customer_id` would have caught this at the source. It would fail the build until the fix is in.

A reconciliation test, `tests/assert_region_revenue_matches_orders.sql`:

```sql
select
    r.total as region_total,
    o.total as orders_total
from (select sum(revenue) as total from {{ ref('region_revenue') }}) r
cross join (select sum(amount) as total from {{ ref('orders') }}) o
where abs(r.total - o.total) > 0.005
```

This catches any fan-out or dropped rows in the report. If some payments legitimately belong to customers with no region, the join will drop them. Switch the join to a `left join` with a `coalesce(region, 'unknown')` bucket so the totals still tie out.
