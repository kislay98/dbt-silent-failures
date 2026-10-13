## 1. Root cause

**`jaffle_shop/models/region_revenue.sql`** joins `raw_customer_regions` without checking that it has one row per customer. The seed has 108 rows for the 100 customers, so some customers have two or more region rows. Each of those customers' payments is duplicated in the join, and `sum(payments.amount)` counts them again.

The counts confirm this:

- `orders` is `count(distinct order_id)`, so it stays at 99. `customers` is `count(distinct customer_id)`, so it stays correct too.
- Revenue is a plain `sum`, so it inflates. The overage is 1846 − 1672 = 174, which is the payment total for orders belonging to customers with duplicate region rows. I inferred this from the numbers, since the seed data isn't in the question.
- The regional orders total is exactly 99. That means a duplicated customer's orders aren't split across two different regions, so the duplicate rows are most likely the same region with different `updated_at` values. Confirm this with the query in the fix below.

`raw_customer_regions.updated_at` is loaded but never used. The model treats the feed as one row per customer when it is a history of snapshots.

The tests didn't catch this because they only check `not_null` and `unique` on `region_revenue.region`, which is the grouping key and is unique by construction. Nothing checks `revenue` against a reconciled total, and nothing checks the regions feed for uniqueness.

## 2. Fix

Take the latest region per customer, then compute revenue from `orders` so the revenue can't fan out. Payments are already summed per order in `orders.amount`, so using it also matches the processor-settled figure.

Create `jaffle_shop/models/staging/stg_customer_regions.sql`:

```sql
select
    customer_id,
    region

from {{ ref('raw_customer_regions') }}

qualify row_number() over (partition by customer_id order by updated_at desc) = 1
```

Then change `jaffle_shop/models/region_revenue.sql` to:

```sql
with orders as (

    select * from {{ ref('orders') }}

),

regions as (

    select * from {{ ref('stg_customer_regions') }}

),

final as (

    select
        regions.region,
        count(distinct orders.customer_id) as customers,
        count(orders.order_id) as orders,
        sum(orders.amount) as revenue

    from orders

    left join regions
        on orders.customer_id = regions.customer_id

    group by regions.region

)

select * from final
```

Before merging, run this to confirm the duplicates are same-region and to see how many rows the dedupe removes:

```sql
select customer_id, count(*) as n, count(distinct region) as n_regions
from raw_customer_regions
group by customer_id
having count(*) > 1;
```

If `n_regions` is greater than 1 for any customer, the latest `updated_at` decides their region. Check that this matches what the regions team intends.

Expected result after the fix: `sum(revenue)` = 1672.00 and `sum(orders)` = 99.

## 3. Tests that would have caught it

Add a uniqueness test on the deduplicated regions model, in `jaffle_shop/models/staging/schema.yml`:

```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
      - name: region
        tests:
          - not_null
```

The `unique` test on the raw seed would also have failed on the 108-row feed, but the dedupe model is the right place to enforce the contract. You could add the same test to `raw_customer_regions` as a warning if the raw feed is expected to contain history.

Add a reconciliation test in `jaffle_shop/tests/assert_region_revenue_matches_orders.sql`. It fails whenever the regional total drifts from the order total, which is the check finance is effectively doing by hand:

```sql
select
    region_total,
    orders_total

from (
    select
        (select sum(revenue) from {{ ref('region_revenue') }}) as region_total,
        (select sum(amount) from {{ ref('orders') }}) as orders_total
)

where abs(region_total - orders_total) > 0.005
```

With the original model this query returns one row (1846 vs 1672), so `dbt build` would have failed.
