claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 23 s | USD 0.004 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`jaffle_shop/models/region_revenue.sql` joins to `raw_customer_regions` without de-duplicating it, so orders from customers with more than one region row are counted once per row.**

The seed `raw_customer_regions` has 102 rows, but `raw_customers` has 100 customers. The seed also has an `updated_at` column, which suggests the feed holds several dated rows per customer. Each extra row for a customer multiplies that customer's order-level payment rows in the join:

```
payments ⋈ orders ⋈ regions   -- regions has 2 rows for customer X
→ every payment for X's orders appears twice
```

The model then does `sum(payments.amount)`, so those payments are double-counted. The arithmetic fits: 1836 − 1672 = 164, which is the total of the duplicated customers' order amounts.

The fan-out does not affect the other columns, which is why finance sees the customer and order counts as correct:

- `count(distinct customer_id)` and `count(distinct order_id)` are immune to duplicate rows.
- Both duplicate rows carry the same region, so each order still lands in exactly one region and the order total stays at 99.

Why the tests missed it:

- `schema.yml` declares `unique` on `region_revenue.region`, but that is the output grain, which the `GROUP BY` guarantees regardless of the fan-out.
- The seed `raw_customer_regions.customer_id` has only `not_null`. Nothing asserts one row per customer, and DuckDB does not enforce uniqueness on seeds.
- No test reconciles `sum(region_revenue.revenue)` with `sum(orders.amount)`.

To confirm before changing anything:

```sql
select customer_id, count(*) as n
from raw_customer_regions
group by 1
having count(*) > 1;

-- should equal 164 if the duplicates are the whole story
select sum(o.amount)
from orders o
where o.customer_id in (
    select customer_id from raw_customer_regions group by 1 having count(*) > 1
);
```

## 2. Fix

Pick one row per customer in the regions CTE, and build revenue from order-level amounts so the payments join is not needed. Replace `jaffle_shop/models/region_revenue.sql` with:

```sql
with regions as (

    {#-
    The regions feed has one row per customer per update; keep the latest
    #}
    select customer_id, region
    from {{ ref('raw_customer_regions') }}
    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

),

orders as (

    select order_id, customer_id, amount from {{ ref('orders') }}

),

final as (

    select
        regions.region,
        count(distinct orders.customer_id) as customers,
        count(distinct orders.order_id) as orders,
        sum(orders.amount) as revenue

    from orders

    join regions
        on orders.customer_id = regions.customer_id

    group by regions.region

)

select * from final
```

Two assumptions to confirm with whoever owns the regions feed:

- The latest `updated_at` is the correct row. If a customer can have two rows with the same `updated_at`, add a deterministic tie-break to the `order by`.
- A customer with a region change should be attributed to their current region, not the region at order time. If history matters, the model needs a different design.

After the change, `sum(revenue)` should be 1672.00 and `sum(orders)` should still be 99.

## 3. Tests that would have caught this

**a) Enforce one row per customer in the seed.** In `jaffle_shop/models/schema.yml`, under `seeds: raw_customer_regions`:

```yaml
      - name: customer_id
        tests:
          - not_null
          - unique
```

This fails the build as soon as the feed has duplicate customers, which is the root cause. If the feed is meant to be historical, this test should target the deduplicated output instead (or be paired with the `qualify` above).

**b) Reconcile revenue against orders.** Add `tests/assert_region_revenue_matches_orders.sql`:

```sql
select rr.revenue_total, o.amount_total
from (select sum(revenue) as revenue_total from {{ ref('region_revenue') }}) rr
cross join (select sum(amount) as amount_total from {{ ref('orders') }}) o
where abs(rr.revenue_total - o.amount_total) > 0.005
```

This is the check finance was effectively running by hand. It also fails if any order has no region, which is a useful signal in its own right. A singular test returns rows on failure, so `dbt build` will report it as an error.

Side note: `jaffle_shop/models/schema.yml` documents `total_order_amount` on `customers`, a column the model does not produce. dbt does not fail on this, but the docs are wrong and should be cleaned up.
