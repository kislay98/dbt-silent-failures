claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 20 s | USD 0.004 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`jaffle_shop/models/region_revenue.sql` fans out payments, and `jaffle_shop/seeds/raw_customer_regions.csv` has more than one row for some customers.**

The `regions` CTE selects every row of `raw_customer_regions` and joins it on `customer_id`:

```sql
join regions
    on orders.customer_id = regions.customer_id
```

The seed is documented as "One row per customer", but it has 108 rows for 100 customers. The `updated_at` column suggests the feed keeps history, so about 8 customers appear twice (possibly with different regions). Each of those customers' orders and payments then join to two region rows, so their payment amounts are counted twice:

- `sum(payments.amount)` is inflated by the duplicated rows. That gives 1846 instead of 1672, a difference of 174.
- `count(distinct orders.customer_id)` and `count(distinct orders.order_id)` are unaffected by duplication, which is why the customer and order counts look right to finance.
- The `unique` test on `region_revenue.region` passes because it only checks the grouped output, not the fan-out.
- Nothing tests `raw_customer_regions.customer_id` for uniqueness. `schema.yml` only applies `not_null` to it, so the duplicates went through `dbt build` unnoticed.

I haven't seen the seed CSV itself, so the duplicates are inferred from the row count (108 against 100 customers) and the fact that revenue is inflated while the counts are not. To confirm, run:

```sql
select customer_id, count(*) as n
from raw_customer_regions
group by customer_id
having count(*) > 1;
```

You should get about 8 customers with `n = 2`, which should account for the 174 difference.

## 2. Fix

Keep one region per customer in `region_revenue.sql`, using the most recent `updated_at`:

```sql
regions as (

    {#-
    The regions feed keeps history; use the latest region per customer
    #}
    select customer_id, region
    from {{ ref('raw_customer_regions') }}
    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

),
```

Two things to confirm with the regions feed owner:

- That "latest `updated_at` wins" is the right rule. If the duplicates are data errors rather than region moves, the correct fix is to correct the seed.
- Whether the seed description ("One row per customer") is the intended contract. If so, the seed should be fixed at the source, and the dedupe above becomes a safety net.

After the change, `sum(region_revenue.revenue)` should equal 1672.00 and the order counts should not change.

## 3. Tests that would have caught this

**a) Uniqueness on the seed key.** In `jaffle_shop/models/schema.yml`, under `raw_customer_regions`:

```yaml
      - name: customer_id
        tests:
          - not_null
          - unique
```

This fails `dbt build` on the current data, which is the point. It would have stopped the bad data at the source.

**b) A reconciliation test against the source of truth.** Add `jaffle_shop/tests/assert_region_revenue_reconciles_to_orders.sql`:

```sql
-- Returns a row (and fails) if region revenue does not add up to order revenue
select
    r.revenue as region_revenue,
    o.amount as order_amount
from (select sum(revenue) as revenue from {{ ref('region_revenue') }}) r
cross join (select sum(amount) as amount from {{ ref('orders') }}) o
where abs(r.revenue - o.amount) > 0.005
```

This would have failed the build with 1846 against 1672. It checks the property finance actually cares about, which the existing per-column `not_null` and `unique` tests do not. It assumes every order's customer has a region, which holds today (the region orders sum to 99, matching the order count). If that stops being true, the test should compare against orders with a region, or be relaxed to allow a known set of unmatched customers.
