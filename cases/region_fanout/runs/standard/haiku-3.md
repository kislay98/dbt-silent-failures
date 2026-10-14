claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 29 s | USD 0.006 | 2026-10-15 | project ad0b16b | prompt d232260b8f43

## 1. Root cause

**`models/region_revenue.sql` joins orders to `raw_customer_regions` without first making that seed one row per customer.** The seed has 108 rows for 100 customers, so some customers appear more than once. Each duplicate fans out that customer's orders and payments, and `sum(payments.amount)` counts those payments again.

The arithmetic matches the finance gap exactly. Region revenue sums to 1846.00 against 1672.00 for orders, a difference of 174.00 that comes from payments belonging to customers with duplicate region rows.

The duplicates are not in different regions. The per-region customer counts sum to 62, which equals the number of customers with orders. If a customer appeared under two regions, that sum would be higher. So the 8 extra rows most likely repeat a customer with the same region and a different `updated_at`, which fits the feed's history-style column. I'm inferring this from the output; the 10-row sample of the seed can't show it. To confirm:

```sql
select customer_id, count(*) as n, count(distinct region) as regions, min(updated_at), max(updated_at)
from raw_customer_regions
group by 1 having count(*) > 1;
```

Why the existing tests missed it:
- `region_revenue.region` `unique` passes because `group by region` collapses the fan-out.
- `customers` and `orders` in the report are `count(distinct ...)`, so they are immune to duplicate rows. That is why finance sees correct counts and wrong revenue.
- The seed has only `not_null` on `customer_id`, with no uniqueness test.
- Nothing checks that the report reconciles to `orders`.

## 2. Fix

Dedupe the regions feed to the latest row per customer, and compute revenue from `orders.amount`. That column is already the sum of each order's payments, so the `payments` join is not needed.

`models/region_revenue.sql`:

```sql
with orders as (

    select * from {{ ref('orders') }}

),

regions as (

    {#-
    The regions feed is loaded as a seed, like the other raw tables.
    It keeps one row per update, so take the latest row per customer
    #}
    select customer_id, region

    from {{ ref('raw_customer_regions') }}

    qualify row_number() over (partition by customer_id order by updated_at desc) = 1

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

After the change, `sum(revenue)` should be 1672.00, `sum(orders)` should be 99, and the customers per region should still sum to 62.

Choices I made that you may want to change:
- **Latest `updated_at` wins.** If two rows share a date with different regions, the result is arbitrary. A tiebreak column would make it deterministic, or the test in section 3 would flag it.
- **The fix lives in the model, not the seed.** The raw feed is kept as received, and the model handles its history.
- If a customer's region should be as of each order date rather than today, this needs a point-in-time join instead. That is a business decision for finance.

## 3. A test that would have caught it

Two tests, in order of how directly they match the symptom.

**a) Reconciliation singular test** (`tests/assert_region_revenue_reconciles_to_orders.sql`). This catches the symptom finance saw, whatever the cause:

```sql
with r as (
    select sum(revenue) as total from {{ ref('region_revenue') }}
),

o as (
    select sum(amount) as total from {{ ref('orders') }}
)

select r.total as region_total, o.total as orders_total
from r
cross join o
where abs(r.total - o.total) > 0.005
```

**b) Uniqueness on the regions feed.** This catches the root cause at its source. Add it to the `raw_customer_regions` seed in `models/schema.yml`:

```yaml
  - name: raw_customer_regions
    columns:
      - name: customer_id
        tests:
          - unique
          - not_null
```

This test fails on the current data, and that is the point. If you want the build to keep running while the feed is fixed, set `severity: warn` on it. Otherwise, move the uniqueness check onto a deduped staging model so it passes after the fix and fails if the dedupe is removed.

## Other things I noticed

- `models/schema.yml` documents `customers.total_order_amount`, but `customers.sql` produces `customer_lifetime_value`. Docs drift is not caught by any test here, so it is worth a fix.
- The `daily_revenue` incremental rebuild and the reconciliation both tie out to 1672 for the four months shown, so that model is not the source of this discrepancy.
