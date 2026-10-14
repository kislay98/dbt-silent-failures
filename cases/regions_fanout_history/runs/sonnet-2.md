claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 16 s | USD 0.053 | 2026-10-14 | project bef6b69 | prompt 46732e565d43

## 1. Root cause

`seeds/raw_customer_regions.csv` (the `raw_customer_regions` table) is not one row per customer. It has an `updated_at` column, so it is a history of region assignments, with one row per customer per region change. `models/region_revenue.sql` joins it on `customer_id` alone, so each payment is repeated once for every region row its customer has.

- A customer with N region rows has their payments counted N times, which inflates revenue and orders (119 vs 99).
- That customer is also counted in N different regions, which gives 68 customers vs 62.
- Finance wants each customer's current region, so only the row with the latest `updated_at` should be used.

The build was green because of gaps in the tests:

- `schema.yml` describes the seed as "One row per customer" but only tests `not_null` on `customer_id` and `region`, never `unique`.
- The `unique` test on `region_revenue.region` is trivially true after `group by region`.
- Nothing reconciles `region_revenue` to `orders`.

I haven't seen the seed data, so this is inferred from the column list and the numbers. You can confirm it with:

```sql
select customer_id, count(*) from raw_customer_regions group by 1 having count(*) > 1
```

The extra 356.00 of revenue should equal the payments of those customers times (rows − 1).

## 2. Fix

Add `models/staging/stg_customer_regions.sql`, which keeps only the current region per customer:

```sql
select
    customer_id,
    region
from {{ ref('raw_customer_regions') }}
qualify row_number() over (
    partition by customer_id
    order by updated_at desc, region
) = 1
```

In `region_revenue.sql`, replace the `regions` CTE body with `select * from {{ ref('stg_customer_regions') }}`. I'd also change `join regions` to `left join regions` and select `coalesce(regions.region, 'UNKNOWN') as region`. That way a customer missing from the feed shows up as a visible bucket instead of silently dropping revenue.

The `region` tiebreaker in the `order by` only makes ties on `updated_at` deterministic. Ties like that are a data problem to raise with the feed owner.

## 3. Tests

In `models/staging/schema.yml`, add a model entry for `stg_customer_regions` with `unique` and `not_null` on `customer_id`. This would have failed the first time a customer had two region rows. If the seed should stay one-row-per-customer, also add a `unique` test on `raw_customer_regions.customer_id`, or fix its description.

Add a reconciliation singular test, `tests/assert_region_revenue_reconciles.sql`:

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
from r, o
where abs(r.revenue - o.revenue) > 0.005
   or r.orders != o.orders
   or r.customers != o.customers
```

On the current data it would have returned a row (2028 vs 1672, 119 vs 99, 68 vs 62). Because `qualify` keeps one region per customer and the join is a left join, it passes after the fix.
