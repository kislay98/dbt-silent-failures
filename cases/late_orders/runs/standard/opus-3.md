claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 35 s | USD 0.202 | 2026-10-14 | project ad0b16b | prompt 0a4fd749d3e5

## 1. Root cause

The bug is in `models/daily_revenue.sql`, triggered by back-dated rows in the `raw_orders` seed. The SQL is fine for normal data, but its incremental window can't see these rows.

**What's wrong in the data:** `raw_orders` has 105 rows. The classic jaffle_shop data has 99, with ids assigned in date order, so id 99 falls in early April 2018. The new rows (ids ~100–105) carry `ordered_at` timestamps in **February 2018**. The sample shows this directly:

| id | ordered_at |
|---|---|
| 102 | 2018-02-13 13:05:51 |
| 103 | 2018-02-17 (from `orders`) |
| 105 | 2018-02-26 (from `orders`) |

These orders arrived weeks after their order date.

**Why `daily_revenue` misses them:** the incremental filter only reprocesses dates in a 3-day lookback:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`max(order_date)` in the existing table is in April 2018. The window therefore only covers early April, and `delete+insert` never rebuilds the February dates. The late February orders exist in `orders` but are never added to `daily_revenue`. The numbers line up exactly:

- Orders: `orders` has 33 in Feb, `daily_revenue` has 27. That's 6 missing, matching 105 − 99.
- Revenue: 480.00 − 415.00 = **65.00 missing**. Overall, 1737.00 − 1672.00 = 65.00, so every missing dollar is in February.
- Jan, Mar and Apr match exactly.

**Why nothing failed:** the tests on `daily_revenue` are only `unique` and `not_null`. A table that is silently missing rows passes both. Nothing reconciles `daily_revenue` against `orders`.

To confirm, run:

```sql
select o.order_date, count(*) as orders, sum(o.amount) as revenue, d.orders as dr_orders, d.revenue as dr_revenue
from orders o left join daily_revenue d using (order_date)
group by o.order_date, d.orders, d.revenue
having d.order_date is null or count(*) <> d.orders
order by 1;
```

## 2. Fix

**Immediate repair:** rebuild the table.

```bash
dbt build --select daily_revenue --full-refresh
```

**Durable fix:** this is about 70 rows aggregated from about 100 orders, so incremental buys nothing. I recommend dropping it:

```sql
{{ config(materialized='table') }}
```

If it has to stay incremental, also rebuild any date whose totals in `orders` no longer match `{{ this }}`:

```sql
    {% if is_incremental() %}
    -- payments can land a day or two after the order, so the last three days are rebuilt on every run;
    -- orders can also be back-filled for older dates, so any date that no longer reconciles is rebuilt too
    where order_date >= (select max(order_date) - interval 3 day from {{ this }})
       or order_date in (
            select src.order_date
            from (
                select order_date, count(*) as orders, sum(amount) as revenue
                from orders
                group by order_date
            ) as src
            left join {{ this }} as tgt
                on src.order_date = tgt.order_date
            where tgt.order_date is null
               or tgt.orders <> src.orders
               or round(tgt.revenue, 2) is distinct from round(src.revenue, 2)
       )
    {% endif %}
```

A cleaner long-term option is to add a load timestamp (e.g. `_loaded_at`) to `raw_orders` and `raw_payments`, then filter on that instead of `order_date`. Then late arrivals are picked up whatever their business date.

## 3. A test that would have caught it

Add `tests/assert_daily_revenue_reconciles_to_orders.sql`:

```sql
-- daily_revenue must match orders exactly for every order date
with expected as (

    select order_date, count(*) as orders, sum(amount) as revenue
    from {{ ref('orders') }}
    group by order_date

),

actual as (

    select order_date, orders, revenue
    from {{ ref('daily_revenue') }}

)

select
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders as expected_orders,
    actual.orders as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue as actual_revenue

from expected
full outer join actual
    on expected.order_date = actual.order_date

where expected.order_date is null
   or actual.order_date is null
   or expected.orders <> actual.orders
   or round(expected.revenue, 2) is distinct from round(actual.revenue, 2)
```

Against last night's data, this would have returned the February dates holding the six back-dated orders, and `dbt build` would have failed.

`dbt_utils.equality` or `dbt_expectations.expect_table_aggregation_to_equal_other_table` do the same job if you already use those packages.
