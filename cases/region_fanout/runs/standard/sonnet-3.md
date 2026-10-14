claude-sonnet-5-5 | claude -p --setting-sources "" --model sonnet --tools "" --output-format json | 18 s | USD 0.086 | 2026-10-15 | project ad0b16b | prompt d232260b8f43

## 1. Root cause

`seeds/raw_customer_regions.csv` (the `raw_customer_regions` seed) is not one row per customer. It has 108 rows for 100 customers, and the extra 8 rows come from customers who moved region. The `updated_at` column shows this: for example, customer 12 has a row dated 2018-04-02 alongside an older one. The seed's schema says "One row per customer", but its tests only check `not_null`, so nothing enforces that.

`models/region_revenue.sql` joins it with `join regions on orders.customer_id = regions.customer_id`. That is a many-to-one assumption that doesn't hold. Every payment for a customer with two region rows is counted twice, once under each region. The overcount is 1846 − 1672 = 174, which is the payment total for those customers.

Why only revenue is wrong:
- `count(distinct customer_id)` and `count(distinct order_id)` ignore the duplicated rows, so they still look right.
- `sum(payments.amount)` is not distinct-safe, so it inflates.
- `orders` and `customers` don't touch the regions seed, so they reconcile at 1672.
- All 44 steps pass because no test checks grain or reconciliation.

## 2. Fix

Reduce the regions to one row per customer, using the latest `updated_at`, before joining. I've also changed the join to a left join so any customer missing from the feed doesn't silently drop revenue. I haven't confirmed whether any are missing. Edit `models/region_revenue.sql`:

```sql
regions as (

    {#-
    The regions feed is loaded as a seed, like the other raw tables.
    It is a history (one row per region change), so keep the latest row per customer.
    #}
    select customer_id, region
    from (
        select
            customer_id,
            region,
            row_number() over (
                partition by customer_id
                order by updated_at desc, region
            ) as rn
        from {{ ref('raw_customer_regions') }}
    )
    where rn = 1

),

final as (

    select
        coalesce(regions.region, 'unknown') as region,
        count(distinct orders.customer_id) as customers,
        count(distinct orders.order_id) as orders,
        sum(payments.amount) as revenue

    from payments

    join orders
        on payments.order_id = orders.order_id

    left join regions
        on orders.customer_id = regions.customer_id

    group by 1

)
```

The `region` in the order-by is only a deterministic tiebreaker. "Latest `updated_at` wins" is my assumption about the feed, so confirm it with the regions feed owner. If the business wants the region as of each order date, join on `updated_at <= order_date` instead.

Expected result after the fix is `sum(revenue) = 1672.00`. A few regions will drop, and the per-region figures will shift for the customers who moved.

## 3. Tests that would have caught it

Enforce the grain at the source, in `models/schema.yml`:

```yaml
  - name: raw_customer_regions
    columns:
      - name: customer_id
        tests:
          - not_null
          - unique
```

That test would have failed on the 8 duplicated customers. Note the seed itself still has duplicates, so for the model to build with the dedup in place, make this a `warn`-severity test or fix the feed. Alternatively, put the `unique` test on a small `stg_customer_regions` model that does the dedup, which is cleaner.

Add a reconciliation test, `tests/assert_region_revenue_reconciles_to_orders.sql`:

```sql
select
    r.total as region_total,
    o.total as orders_total
from (select sum(revenue) as total from {{ ref('region_revenue') }}) r
cross join (select sum(amount) as total from {{ ref('orders') }}) o
where abs(r.total - o.total) > 0.005
```

This one fails on the current build (1846 vs 1672). It also guards against any future fan-out or dropped rows in `region_revenue`.
