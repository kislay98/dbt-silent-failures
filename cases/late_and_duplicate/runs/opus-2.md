claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 57 s | USD 0.190 | 2026-10-14 | project f769fdf | prompt c05f6595f117

# Two separate defects

There are two independent problems. Together they explain all three numbers.

| | Total | vs. settlement 1737.00 |
|---|---|---|
| `orders` / `stg_payments` | 1917.00 | **+180.00**: duplicated payment rows |
| `daily_revenue` | 1686.00 | **−51.00**: the backfilled February orders are missing |

## 1. Root cause

### A. `seeds/raw_payments.csv`: last night's load re-inserted existing payments under new ids

Every model that reads `stg_payments` sums each payment twice. That includes `orders`, `customers`, and `region_revenue`.

- Order 1 shows 20.00, but `daily_revenue` (built before the load) still shows 10.00 for 2018-01-01, which is what the customer was charged.
- The mismatches are not limited to February. 2018-01-14, 01-20, 01-28, 03-05, 03-12, 03-23 and 03-27 all have the **same order count** in both tables but a higher amount in `orders`. Only extra payment rows can do that. Missing orders cannot.
- `unique_stg_payments_payment_id` passes because the duplicates were given new `id`s. No test looks at the payment's content: order, method, and amount.

The arithmetic agrees. Excess on dates with matching counts (Jan 74 + Mar 57 + Feb 02-10/02-19 17) = 148. Excess on the backfilled orders (83 in `orders` − 51 true) = 32. Total 148 + 32 = **180 = 1917 − 1737**.

Confirm before deleting anything, since I don't have the seed:

```sql
select order_id, payment_method, amount, count(*) as n, list(id order by id) as ids
from raw_payments
group by 1, 2, 3
having count(*) > 1
order by order_id;
-- the "extra" amount/100 across these rows should be exactly 180.00
```

### B. `models/daily_revenue.sql`: the incremental window cannot see backfills

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The window is anchored on the **latest order_date already in the table**, which is in April 2018. A load that adds or changes rows for February never falls inside it. The result:

- The 6 backfilled February orders (33 vs 27; 02-03 and 02-22 are missing entirely) never reach `daily_revenue`.
- Every date outside the last 3 days keeps its values from whenever it was first built. That is why it accidentally still shows the *correct* pre-duplicate amounts for old dates.

Its total is 1737 − 51 = 1686, where 51 is the true value of the 6 missing orders.

The `delete+insert` on `order_date` and the `unique` test both work as designed. They just only ever see the most recent 3 days.

## 2. Fix

**Data:** remove the duplicate rows from `seeds/raw_payments.csv`, keeping the lowest `id` per duplicate group from the query above. Also fix whatever produced them upstream, since the backfill job evidently re-emits existing payments.

I'd fix this at the source rather than dedupe in `stg_payments`. A customer can legitimately make two equal payments with the same method on one order, so a blanket `distinct` on (order_id, method, amount) could silently drop real money. If you do need a guard in staging until the loader is fixed, dedupe on a true source key (processor transaction id) if one exists.

**Model:** `daily_revenue` is about 100 rows, so incrementality buys nothing and costs correctness. Make it a table:

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

If it must stay incremental, the window has to be driven by *when rows were loaded*, not by `order_date`. That means adding a `_loaded_at` column to the raw tables and rebuilding every `order_date` touched since the last run. That needs a loader change, because no such column exists today.

**Rebuild:**

```bash
dbt seed -s raw_payments --full-refresh
dbt build --full-refresh -s +daily_revenue +customers +region_revenue
```

Afterwards, `sum(amount)` from `orders` and `sum(revenue)` from `daily_revenue` should both equal 1737.00, and order 1 should show 10.00.

## 3. Tests that would have caught it

`tests/daily_revenue_reconciles_to_orders.sql` fails on gap B. It would have flagged all 16 dates above:

```sql
-- daily_revenue must agree with orders for every order_date
with o as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by 1
),
d as (
    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}
)
select coalesce(o.order_date, d.order_date) as order_date,
       o.orders as expected_orders, d.orders as actual_orders,
       o.revenue as expected_revenue, d.revenue as actual_revenue
from o
full outer join d using (order_date)
where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

`tests/no_duplicate_payments.sql` catches A. Set it to `warn` if equal split payments are legitimate in your business, but either way it would have surfaced last night's load:

```sql
{{ config(severity='error') }}

select order_id, payment_method, amount, count(*) as n
from {{ ref('stg_payments') }}
group by 1, 2, 3
having count(*) > 1
```

The strongest guard against A is a reconciliation test against the processor settlement total per period, if that feed can be loaded as a source. That is the check finance did by hand.

## Smaller issues I noticed

- `raw_customer_regions` has an `updated_at` column, which suggests a history feed. It is only tested for `not_null`. If a customer ever gets two rows, `region_revenue` will double-count that customer's revenue. Add `unique` on `customer_id`, or pick the latest row per customer.
- `schema.yml` documents `customers.total_order_amount`, but the model outputs `customer_lifetime_value`. That's doc drift only, not a numbers bug.
