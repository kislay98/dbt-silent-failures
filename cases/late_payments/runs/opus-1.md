claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 31 s | USD 0.135 | 2026-10-14 | project f769fdf | prompt e8c77caade40

# Root cause: late payments for February orders never reached the incremental `daily_revenue`

## 1. Root cause

**File:** `jaffle_shop/models/daily_revenue.sql`, the incremental filter:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

**What is wrong in the data:** `raw_payments` received additional payments for February orders long after those dates had been aggregated. Examples are payment ids 114, 115 and 116 for orders 30, 33 and 36. Their ids sit far above the original payment ids (35, 38, 41), so they were loaded later.

Here is how that produces the gap:

- `stg_payments` and `orders` are views and tables that rebuild fully every night, so `orders.amount` picks up the late payments. That gives 485.00, which matches the settlement.
- `daily_revenue` is incremental, and the filter only rebuilds dates within 3 days of `max(order_date)` already in the table. That max is now `2018-04-09`, so only 2018-04-06 onward is recomputed. The February rows still hold the revenue as it stood when they were last inside the window.
- The order counts still agree (27 = 27) because no orders were added, only payments. The model's comment says payments land "a day or two" after the order, but these landed weeks later.

The per-day differences in your sample add up exactly to the gap:

| date | orders | daily_revenue | missing |
|---|---|---|---|
| 02-02 | 30 | 25 | 5 |
| 02-04 | 37 | 25 | 12 |
| 02-10 | 55 | 47 | 8 |
| 02-11 | 23 | 8 | 15 |
| 02-16 | 23 | 17 | 6 |
| 02-17 | 43 | 34 | 9 |
| 02-21 | 29 | 18 | 11 |
| 02-26 | 36 | 32 | 4 |
| **total** | | | **70.00** (485 − 415) |

The all-time totals show the same gap: 1742 − 1672 = 70. That means the drift is entirely in February.

None of the 29 tests could catch this. They are all single-model row checks (`unique`, `not_null`, `accepted_values`), and nothing reconciles `daily_revenue` against `orders`.

## 2. Fix

**Immediate repair:** rebuild the table from scratch.

```bash
dbt build --select daily_revenue --full-refresh
```

**Durable fix (recommended):** the model aggregates a small table and runs in about 0.03s, so incrementality adds risk without any real benefit. Make it a plain table:

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

**If it must stay incremental:** a wider lookback window doesn't solve this, because payments can arrive arbitrarily late. `raw_payments` also has no load timestamp to drive the increment. The workable option is to recompute every date whose aggregate has changed:

```sql
{% if is_incremental() %}
having not exists (
    select 1 from {{ this }} t
    where t.order_date = orders.order_date
      and t.orders = count(*)
      and round(t.revenue, 2) = round(sum(orders.amount), 2)
)
{% endif %}
```

This still scans all of `orders` on every run, which is another reason to prefer the plain table. The better long-term fix is for ingestion to add a `_loaded_at` column to `raw_payments`.

## 3. Test that would have caught it

Add a singular reconciliation test as `tests/assert_daily_revenue_reconciles_to_orders.sql`:

```sql
-- daily_revenue must match orders exactly, per day
with o as (

    select order_date, count(*) as orders, round(sum(amount), 2) as revenue
    from {{ ref('orders') }}
    group by order_date

),

d as (

    select order_date, orders, round(revenue, 2) as revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders  as orders_orders,
    d.orders  as daily_orders,
    o.revenue as orders_revenue,
    d.revenue as daily_revenue
from o
full outer join d on o.order_date = d.order_date
where o.orders  is distinct from d.orders
   or o.revenue is distinct from d.revenue
```

Against the current warehouse, this test fails with the 8 February rows listed above. You could additionally reconcile `sum(stg_payments.amount)` by month against the processor settlement.

**Unrelated issue:** `raw_customer_regions` has an `updated_at` column but no `unique` test on `customer_id`. If the feed keeps history, `region_revenue` will double-count revenue. Consider adding a `unique` test there too.
