claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 53 s | USD 0.184 | 2026-10-14 | project f769fdf | prompt c05f6595f117

# Two separate bugs that both pass `dbt build`

There are two independent faults. Each one explains part of the gap, and together they account for it exactly:

| | Total |
|---|---|
| Settlement (truth) | 1737.00 |
| `orders` | 1917.00 (**+180** from duplicate payments) |
| `daily_revenue` | 1686.00 (**−51** from 6 backfilled orders that are missing) |

## 1. Root cause

### A. `raw_payments` has duplicate payment rows (data). This inflates `orders`, `customers` and `region_revenue`.

- `stg_payments` has 131 rows across 105 orders. The baseline is roughly one payment per order plus a few legitimate split payments.
- Order 1 has a second 10.00 payment row, so it shows 20.00.
- Look at the dates that were *not* touched by the backfill (Jan, Mar, 02-10, 02-19). Order counts match between `orders` and `daily_revenue`, but `orders` is higher every time. Those excesses sum to exactly 180, which is 1917 − 1737.
- `unique_stg_payments_payment_id` passes because the duplicates were loaded with **new `id`s**. Uniqueness on the surrogate key can't see a re-delivered payment.

I can't see the seed CSV, so this is inferred from the numbers. Confirm it with:

```sql
select order_id, payment_method, amount, count(*) as n, list(id) as ids
from raw_payments
group by all
having count(*) > 1
order by order_id;
```

The surplus amounts should sum to 18000 cents.

### B. `models/daily_revenue.sql`: the incremental filter can't pick up backfills (code). This deflates the dashboard.

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

This only reprocesses the 3 days before the latest date already in the table, which is in April 2018. The February orders that were backfilled last night have old `order_date`s, so they are never selected. The evidence:

- Feb has 33 orders in `orders` but 27 in `daily_revenue`.
- 02-03 and 02-22 are missing entirely.
- 02-08, 02-13, 02-17 and 02-26 are each short by one order.
- April, the only month inside the window, matches exactly.

The rows in `daily_revenue` for older dates are therefore frozen from before last night's load. That is also why they show order 1 as 10.00: they predate the duplicate payments. They are stale, not correct. The 6 missing orders are worth 51 at true value, which gives 1686 + 51 = 1737.

`unique_daily_revenue_order_date` and the `not_null` tests all pass because nothing is null or duplicated. The rows are just wrong.

## 2. Fix

**Data:** remove the duplicate payment rows from `raw_payments`, keeping the original `id` for each. Do this at the source or seed, then have the loader dedupe on the processor's transaction reference.

Don't "fix" this in `stg_payments` by deduping on `(order_id, payment_method, amount)`. A real customer can make two identical payments on one order, so that would silently drop legitimate revenue. Dedupe in staging only on a true natural key such as a processor transaction id, if the feed has one.

**Code:** `daily_revenue` aggregates about 100 rows, so the incremental logic buys nothing and loses backfills. Make it a table:

```sql
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

Update its description in `schema.yml` to drop "Incremental, rebuilds the last three days on each run".

If it has to stay incremental at scale, add a `_loaded_at` column to `raw_orders` and `raw_payments` and filter on that rather than `order_date`. That way late-arriving rows reprocess their own dates.

Then rebuild everything:

```sh
dbt build --full-refresh
```

`--full-refresh` matters if you keep it incremental, because the existing table holds stale rows. After the rebuild, `sum(orders.amount)` and `sum(daily_revenue.revenue)` should both be 1737.00.

## 3. Tests that would have caught it

**`tests/assert_daily_revenue_reconciles_to_orders.sql`** fails on any date where the rollup drifts from its source. It would have flagged the 16 dates above:

```sql
with src as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

)

select *
from src
full outer join {{ ref('daily_revenue') }} as d using (order_date)
where d.order_date is null
   or src.order_date is null
   or src.orders <> d.orders
   or abs(src.revenue - d.revenue) > 0.005
```

**`tests/assert_no_duplicate_payments.sql`** catches re-delivered payments that the `payment_id` uniqueness test can't see. Use `severity: warn` if legitimate identical split payments are possible:

```sql
select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by order_id, payment_method, amount
having count(*) > 1
```

The strongest guard is a reconciliation test against the processor settlement total, if you can load it as a source or seed. That compares the warehouse to an external truth rather than to itself.

## Also worth fixing

These are latent issues, not part of this incident:

- **`raw_customer_regions`** is described as "one row per customer" but has an `updated_at` column and no `unique` test on `customer_id`. If the feed ever sends history rows, `region_revenue` will fan out and double-count. Add `unique` on `customer_id`.
- **`schema.yml`** documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`.
