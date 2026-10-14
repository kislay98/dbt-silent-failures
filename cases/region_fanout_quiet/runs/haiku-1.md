claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 25 s | USD 0.004 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

The fan-out is in the regions seed, `jaffle_shop/seeds/raw_customer_regions.csv` (loaded as `raw_customer_regions`), and it is joined in `jaffle_shop/models/region_revenue.sql`.

- The seed has a `updated_at` column, so it is a dated feed with history, not one row per customer. The load log shows `INSERT 102` for `raw_customer_regions` but only 100 customers in `raw_customers`. At least two customers have more than one region row.
- `region_revenue.sql` joins `payments → orders → regions` on `customer_id` with no dedup. For a customer with two region rows, every one of their payments is counted once per row. Their revenue is doubled.
- `orders` is unaffected because the model uses `count(distinct orders.order_id)`. `customers` is also unaffected because it uses `count(distinct orders.customer_id)`. Only `sum(payments.amount)` is inflated, which is why the counts look right and only revenue is off.
- The gap is 1836 − 1672 = 164. That is the revenue of the duplicated customers' orders, which is consistent with this explanation. I haven't run the data, so confirm with:

```sql
select customer_id, count(*) as n, min(region), max(region)
from raw_customer_regions
group by customer_id
having count(*) > 1;
```

Nothing catches this because the seed has no uniqueness test on `customer_id`. The `unique` test on `region_revenue.region` passes trivially because of the `group by`. The seed description, "One row per customer", is also false for this data.

## 2. Fix

Add a staging model that keeps the latest region per customer, and point `region_revenue` at it. I'm choosing "latest `updated_at` wins" as the rule. Please confirm that with whoever owns the regions feed.

`jaffle_shop/models/staging/stg_customer_regions.sql`:
```sql
with source as (

    select * from {{ ref('raw_customer_regions') }}

),

latest as (

    select *
    from source
    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

)

select customer_id, region from latest
```

In `jaffle_shop/models/region_revenue.sql`, change the regions CTE:
```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

Also correct the seed description in `models/schema.yml`. It should say the feed has history and that `customer_id` is not unique in the raw seed. Then add the test below.

Caveat: `region_revenue` only includes orders whose customer has a region row. If some customers have no region, the report total will stay below 1672 even after the fix. Check with:
```sql
select sum(amount) from orders
where customer_id not in (select customer_id from stg_customer_regions);
```
If that is non-zero, decide whether to add an "UNASSIGNED" region row. Don't leave it silently dropped.

## 3. Tests that would have caught this

Primary test, in `models/staging/schema.yml` or the seed's `schema.yml`. It fails on the raw seed as soon as a customer has two rows:

```yaml
seeds:
  - name: raw_customer_regions
    columns:
      - name: customer_id
        tests:
          - not_null
          - unique   # fails today: 102 rows for 100 customers
```

Reconciliation test, in `tests/region_revenue_matches_orders.sql`. It checks the number finance actually cares about:

```sql
with region_total as (
    select sum(revenue) as total from {{ ref('region_revenue') }}
),

orders_total as (
    select sum(amount) as total
    from {{ ref('orders') }}
    where customer_id in (select customer_id from {{ ref('stg_customer_regions') }})
)

select *
from region_total, orders_total
where abs(region_total.total - orders_total.total) > 0.005
```

A singular test fails when it returns rows, so this fails whenever the report and the orders total disagree. Note that it compares against orders with a mapped region, which is the same scope as the report. If you add an UNASSIGNED row, change the filter to include all orders.

Also noticed, unrelated to this bug: `models/schema.yml` documents `total_order_amount` on `customers`, but the model produces `customer_lifetime_value`. The schema test does not check documented columns against the model, so that drift went unnoticed in the same way.
