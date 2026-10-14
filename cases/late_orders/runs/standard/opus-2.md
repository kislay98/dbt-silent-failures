claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 35 s | USD 0.199 | 2026-10-14 | project ad0b16b | prompt 0a4fd749d3e5

# Diagnosis: `daily_revenue` is missing late-arriving February orders

## 1. Root cause

The bug is in **`models/daily_revenue.sql`**. Its incremental filter does not reach back far enough for the new data in **`seeds/raw_orders.csv`** (and its payments in **`seeds/raw_payments.csv`**).

**What changed in the data:** six orders were added with `ordered_at` timestamps in **February 2018**. The evidence:

- `raw_orders` has 105 rows and `raw_payments` has 119. The stock jaffle_shop seeds have 99 and 113, so there are six extra of each.
- The sampled high IDs all carry February dates: order 102 is 2018-02-13, 103 is 2018-02-17 and 105 is 2018-02-26.
- The gap matches exactly. February in `orders` has 33 orders and 480.00; in `daily_revenue` it has 27 orders and 415.00. That is a difference of **6 orders and 65.00**, which is also the overall gap (1737 − 1672). January, March and April match exactly.

**Why the model never picked them up:** `daily_revenue` is incremental with `delete+insert` on `order_date`, and it only reprocesses this window:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`max(order_date)` is in April 2018, so each run rebuilds only the last few days of April. Any order whose `order_date` falls before that window is never reprocessed. The comment assumes only *payments* arrive late and only by a day or two. Here, whole *orders* arrived late and were backdated by about two months.

**Why every test passed:** the existing tests on `daily_revenue` only check `unique` and `not_null`. Stale rows still satisfy both. Upstream, `orders`, `customers` and `region_revenue` are full tables and are correct: `region_revenue` sums to 1737, and `customers` lifetime value is 1737.

To confirm which days are stale:

```sql
with src as (
    select order_date, count(*) as orders, sum(amount) as revenue
    from orders group by 1
)
select src.order_date, src.orders, d.orders as dr_orders, src.revenue, d.revenue as dr_revenue
from src
left join daily_revenue d on d.order_date = src.order_date
where d.order_date is null
   or d.orders <> src.orders
   or d.revenue is distinct from src.revenue
order by 1;
```

## 2. Fix

**Repair the data now:**

```bash
dbt build --select daily_revenue --full-refresh
```

**Prevent a recurrence:** this model aggregates about 100 rows from `orders`, and `orders` is already rebuilt in full on every run. Being incremental saves nothing and adds a correctness risk, so I recommend making it a table:

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

If it must stay incremental, keep the lookback but also rebuild any date whose totals no longer match the source:

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
   or order_date in (
        select src.order_date
        from (select order_date, count(*) as orders, sum(amount) as revenue
              from {{ ref('orders') }} group by 1) src
        left join {{ this }} t on t.order_date = src.order_date
        where t.order_date is null
           or t.orders <> src.orders
           or t.revenue is distinct from src.revenue
   )
{% endif %}
```

A better long-term option is to add a `loaded_at` column to `raw_orders` and `raw_payments` and filter on load time instead of business date. Today there is no such column, so the model cannot tell when a row arrived.

## 3. Test that would have caught it

Add a singular reconciliation test as `tests/assert_daily_revenue_reconciles_to_orders.sql`:

```sql
-- daily_revenue must match orders per order_date; catches stale incremental windows
with src as (

    select order_date, count(*) as orders, coalesce(sum(amount), 0) as revenue
    from {{ ref('orders') }}
    group by order_date

),

tgt as (

    select order_date, orders, coalesce(revenue, 0) as revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(src.order_date, tgt.order_date) as order_date,
    src.orders  as expected_orders,
    tgt.orders  as actual_orders,
    src.revenue as expected_revenue,
    tgt.revenue as actual_revenue

from src
full outer join tgt
    on src.order_date = tgt.order_date

where src.order_date is null
   or tgt.order_date is null
   or src.orders <> tgt.orders
   or abs(src.revenue - tgt.revenue) > 0.005
```

`dbt build` runs this after `daily_revenue`. Last night it would have failed on the February dates that hold the six late orders, instead of reporting PASS=44.
