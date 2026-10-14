claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 32 s | USD 0.191 | 2026-10-14 | project ad0b16b | prompt 0a4fd749d3e5

# Diagnosis: `daily_revenue` is missing six backdated February orders

## 1. Root cause

The code didn't change. What changed is the data: `raw_orders` (and the matching `raw_payments`) received orders that are **dated in February but were loaded after April**. The incremental filter in `models/daily_revenue.sql` cannot pick them up.

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
{% endif %}
```

How the filter fails:

- The window is anchored to `max(order_date)` already in the target, which is early April 2018.
- Each run therefore only rebuilds roughly the last three days of April.
- A row dated `2018-02-13` falls outside the window, so it is never inserted. With `delete+insert`, the February rows that already exist are never deleted and recomputed either.
- The comment says the window is for late *payments*. It only covers lateness of up to three days relative to the newest order, not orders or payments backdated by weeks.

**Evidence:**

- **Only February differs.** January, March and April match `orders` exactly. February is short by 6 orders (27 vs 33) and 65.00 (415 vs 480). The overall gap is the same 65.00 (1672 vs 1737).
- **Late order IDs carry February dates.** `raw_orders` has 105 rows, but the original jaffle_shop sequence ends around id 99 on 2018-04-09. The sampled high ids have February dates while lower ids are already in April:
  - `102 → 2018-02-13`
  - `103 → 2018-02-17`
  - `105 → 2018-02-26`
- **Everything not incremental agrees with `orders`.** `customers` lifetime value is 1737. The `region_revenue` regions sum to 75+349+356+105+130+411+311 = 1737. `orders` is a full-rebuild table and is correct.
- **No test compares the two models.** The `daily_revenue` tests are only `unique` and `not_null` on its own rows. A table that is missing rows or has stale ones passes all of them, so `PASS=44` is expected.

To confirm the exact six orders:

```sql
select o.order_id, o.order_date, o.amount, d.orders as dr_orders, d.revenue as dr_revenue
from orders o
left join daily_revenue d using (order_date)
where o.order_id > 99
order by o.order_id;
```

Where a late order landed on a date that already had orders, that date's row has a stale count and revenue. Where it landed on a new date, the row is missing entirely.

## 2. Fix

**Immediate repair** (restores February to 480.00 and the total to 1737.00):

```bash
dbt build -s daily_revenue --full-refresh
```

**Durable fix.** `daily_revenue` is about 69 rows aggregated from about 105 orders, so the incremental logic saves nothing and adds risk. Make it a plain table in `models/daily_revenue.sql`:

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

If it must stay incremental at real scale, the window has to be driven by *load time* rather than `order_date`. That means adding a `loaded_at` or `_etl_updated_at` column to the raw feeds and rebuilding every `order_date` touched since the last run. A wider fixed lookback only shrinks the gap; it doesn't close it. The `schema.yml` description ("rebuilds the last three days on each run") should be updated to match whichever option you choose.

## 3. Test that would have caught it

Add a reconciliation test, `tests/assert_daily_revenue_matches_orders.sql`:

```sql
-- daily_revenue must agree with orders for every order_date
with expected as (

    select
        order_date,
        count(*) as orders,
        round(sum(amount), 2) as revenue
    from {{ ref('orders') }}
    group by order_date

),

actual as (

    select
        order_date,
        orders,
        round(revenue, 2) as revenue
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
where expected.orders is distinct from actual.orders
   or expected.revenue is distinct from actual.revenue
```

Against last night's tables, this returns the February dates that are off. `dbt build` would then fail with a non-zero exit code instead of reporting `PASS=44`. The full outer join also catches dates that are missing from either side.
