claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 24 s | USD 0.173 | 2026-10-15 | project ad0b16b | prompt dee3d414f358

## 1. Root cause

**`models/region_revenue.sql` joins to `raw_customer_regions` as if it had one row per customer, but it doesn't.**

`raw_customer_regions` has **106 rows for 100 customers**, and it has an `updated_at` column. The feed is a region history: when a customer moves, the feed adds a new row and keeps the old one. The seed description in `models/schema.yml` ("One row per customer") is wrong, and the only tests on the seed are `not_null`. Nothing enforces uniqueness on `customer_id`.

In `region_revenue.sql`, this join fans out:

```sql
join regions
    on orders.customer_id = regions.customer_id
```

Every payment from a customer with N region rows is counted N times:

- **Revenue:** 2028 − 1672 = **356** is payment value counted more than once.
- **Customers:** 68 − 62 = **6** extra customers. Those customers are counted once in each region they have ever lived in.
- **Orders:** the region orders also sum to 119, against 99 real orders.

None of the tests could see this. `unique(region)` and `not_null(revenue)` still pass on inflated numbers. The other models (`orders`, `customers`, `daily_revenue`) don't touch the regions feed, and they reconcile to 1672.

To confirm against the warehouse:

```sql
select customer_id, count(*) as n, list(region order by updated_at) as regions
from raw_customer_regions
group by 1 having count(*) > 1;
```

## 2. Fix

Finance wants each customer's **current** region, so keep only the latest row per customer. Add a staging model so other models can reuse it:

**`models/staging/stg_customer_regions.sql`** (new)
```sql
with source as (

    {#-
    The regions feed keeps history: a new row is added each time a customer's region changes
    #}
    select * from {{ ref('raw_customer_regions') }}

),

current_region as (

    select
        customer_id,
        region,
        updated_at

    from source

    qualify row_number() over (
        partition by customer_id
        order by updated_at desc, region
    ) = 1

)

select * from current_region
```

The `region` tie-breaker only keeps the result deterministic. If one customer has two rows with the same `updated_at`, that is a feed problem and should be raised with the feed owner.

**`models/region_revenue.sql`**: change the `regions` CTE to:
```sql
regions as (

    select * from {{ ref('stg_customer_regions') }}

),
```

Also correct the seed description in `models/schema.yml` to something like "One row per customer per region change, from the regions feed".

After a rebuild, `sum(revenue)` should be 1672.00 and `sum(customers)` should be 62. I don't have the seed data, so I haven't run this. One more thing to check: an inner join would silently drop any ordering customer who has no region row at all. The tests below catch that case too.

## 3. Tests that would have caught it

**Uniqueness on the grain.** Add this to `models/staging/schema.yml`:
```yaml
  - name: stg_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
```
If `region_revenue` had joined to a model with this test, the fan-out could not have happened.

**Reconciliation test.** This one catches the symptom whatever the cause (fan-out, dropped customers, etc.).

**`tests/assert_region_revenue_reconciles.sql`**
```sql
-- region_revenue must add up to orders: same revenue, and each ordering customer in exactly one region
with region_totals as (

    select
        sum(revenue) as revenue,
        sum(customers) as customers,
        sum(orders) as orders
    from {{ ref('region_revenue') }}

),

order_totals as (

    select
        sum(amount) as revenue,
        count(distinct customer_id) as customers,
        count(*) as orders
    from {{ ref('orders') }}

)

select *
from region_totals
cross join order_totals
where abs(region_totals.revenue - order_totals.revenue) > 0.01
   or region_totals.customers != order_totals.customers
   or region_totals.orders != order_totals.orders
```

On the current build, this test fails with 2028 vs 1672, 68 vs 62, and 119 vs 99.
