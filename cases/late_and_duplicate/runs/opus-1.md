claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 60 s | USD 0.196 | 2026-10-14 | project f769fdf | prompt c05f6595f117

# Diagnosis

There are two separate faults. Each one explains one of the two wrong numbers, and together they account for every dollar:

| | Value | vs. settlement 1737 |
|---|---|---|
| `orders` | 1917 | **+180**: duplicated payment rows |
| `daily_revenue` | 1686 | **−51**: backfilled February orders never picked up |

`daily_revenue` happens to be correct for old dates. It was built before last night's load and was never rebuilt for them, which is why order 1 is 10.00 there and 20.00 in `orders`.

## 1. Root cause

### A. Duplicate payments in `raw_payments` (seed data) inflate `orders` and `customers`

- `stg_payments` has 131 payment rows. That is 18 more than the original 113, but only 6 new orders arrived: `daily_revenue` is missing exactly 6 February orders, on 02-03, 02-08, 02-13, 02-17, 02-22 and 02-26.
- The other ~12 new payment rows attach to **existing** orders. Their amounts exactly match payments already there:
  - order 1 (2018-01-01): 10 → 20
  - 2018-02-19: 8 → 16
  - 2018-03-05: 14 → 28
- The inflation also hits January and March, not just February. It totals 74 (Jan) + 57 (Mar) + 49 (Feb), which is 180 = 1917 − 1737.
- The backfill re-sent existing payments with new surrogate `id`s. Because the ids are new, `unique_stg_payments_payment_id` passes. `orders.sql` then does `sum(amount) ... group by order_id` and double-counts them.
- `not_null` and `unique` tests cannot see this. Nothing in the project checks that the same payment wasn't loaded twice.

I don't have the seed rows, so confirm this with:

```sql
select order_id, payment_method, amount, count(*) as n, list(id order by id) as ids
from raw_payments
group by 1, 2, 3
having count(*) > 1
order by order_id;
```

Expected result: about 12 groups, each pairing an original id with an id above 113, with the duplicated amounts summing to 18000 (cents). Order 1 should be the first row.

### B. `models/daily_revenue.sql`: the incremental lookback can't see backfills

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

- This only rebuilds the 3 days before the latest `order_date`, which is in April.
- Anything that arrives late for an older date is never processed. That covers the backfilled February orders (−51) and, coincidentally, the duplicate payments.
- `delete+insert` on `order_date` is fine. The filter is what's wrong: it keys on *event date*, but the backfill changed *old* dates.
- The raw tables have no load timestamp to drive a better filter.

## 2. Fix

**Data:** remove the duplicate rows from `seeds/raw_payments.csv`, using the ids returned by the query above, and fix the backfill job upstream so it doesn't re-emit existing payments. I would not dedupe on `(order_id, payment_method, amount)` inside `stg_payments`. A customer can legitimately pay twice with the same method and amount, so that would silently drop real payments.

**Model:** the table is tiny, so stop making it incremental.

```sql
-- models/daily_revenue.sql
{{ config(materialized='table') }}

with orders as (

    select * from {{ ref('orders') }}

),

final as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from orders

    group by order_date

)

select * from final
```

If it must stay incremental, it needs a load timestamp (e.g. `_loaded_at` on the raw tables). The filter would then reprocess every `order_date` touched by rows loaded since the last run, and every backfill would need a `--full-refresh`.

**Rebuild:**

```
dbt seed -s raw_payments --full-refresh
dbt build --full-refresh -s raw_payments+
```

Expected after the rebuild: `sum(orders.amount)` = `sum(daily_revenue.revenue)` = 1737.00, with 105 orders, and order 1 = 10.00.

## 3. Tests that would have caught it

```sql
-- tests/assert_daily_revenue_reconciles_to_orders.sql
-- daily_revenue must match orders for every date; catches incremental drift
with o as (
    select order_date, count(*) as n, sum(amount) as amount
    from {{ ref('orders') }}
    group by 1
),

d as (
    select order_date, orders as n, revenue as amount
    from {{ ref('daily_revenue') }}
)

select coalesce(o.order_date, d.order_date) as order_date, o.n, d.n, o.amount, d.amount
from o
full outer join d on o.order_date = d.order_date
where o.n is distinct from d.n
   or abs(coalesce(o.amount, 0) - coalesce(d.amount, 0)) > 0.005
```

This would have failed last night on 16 dates.

```sql
-- tests/assert_no_duplicate_payments.sql
-- the same payment loaded twice under a new id
select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

Set this one to `severity: warn` and add an allow-list if genuine repeat payments exist. The strongest check is a singular test that compares monthly `sum(amount)` against the processor settlement totals, if finance can expose them as a source or seed.

## Related latent bug, not causing these numbers

`raw_customer_regions` is documented as "one row per customer". However, it has an `updated_at` column and no uniqueness test, so if the feed ever sends history rows, `region_revenue` will fan out payments. Add this:

```yaml
seeds:
  - name: raw_customer_regions
    columns:
      - name: customer_id
        data_tests:
          - unique
          - not_null
```

Also add a reconciliation test asserting that `sum(region_revenue.revenue)` = `sum(stg_payments.amount)`.
