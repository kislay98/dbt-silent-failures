claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 38 s | USD 0.144 | 2026-10-14 | project f769fdf | prompt 0e22716f6c28

# Late-arriving February orders that the incremental `daily_revenue` model never reprocessed

## 1. Root cause

**Data:** `seeds/raw_orders.csv` gained **6 new orders with February `ordered_at` dates**. Those rows were loaded long after `daily_revenue` had already built February.

**Code:** `models/daily_revenue.sql` is incremental with `delete+insert` on `order_date`, and it only rebuilds a window anchored to the data it already holds:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

`max(order_date)` in `{{ this }}` is in April 2018. So each run rebuilds only the last few April days. Any new row dated earlier than that window is never selected. The run didn't fail; it just skipped them.

The numbers line up with this:

- **Order counts:** `orders` has 105 rows and `daily_revenue` sums to 99. The 6 missing orders are spread across 2018-02-03, 02-08, 02-13, 02-17, 02-22 and 02-26, which is exactly your diff output.
- **Revenue:** 480 − 415 = 1737 − 1672 = **65.00**. All of the missing revenue belongs to those 6 orders, and every other month matches.
- **Why tests passed:** every test checks a single model on its own (`unique`, `not_null`). Nothing compares `daily_revenue` to `orders`.
- **Likely order IDs:** the stock jaffle_shop seed has 99 orders (ids 1–99), so the new rows are probably ids 100–105. You can confirm with:
  ```sql
  select order_id, ordered_at, status from stg_orders
  where order_date in ('2018-02-03','2018-02-08','2018-02-13','2018-02-17','2018-02-22','2018-02-26')
  order by order_id desc;
  ```

The model's comment says late *payments* are handled. That is only true if they land within 3 days of the newest order date. It has nothing to do with when the data actually arrived. Late payments for older orders would be silently dropped in the same way.

## 2. Fix

**First, repair the table now:**
```bash
dbt build --select daily_revenue --full-refresh
```

**Then stop it from happening again.** With about 100 orders, incremental logic gains nothing, so make the model a plain table:

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

If it has to stay incremental at real volume, the window must follow **when rows arrived**, not their business date. That means adding a load timestamp (e.g. `_loaded_at`) to `raw_orders` and `raw_payments`, carrying it through staging into `orders`, and rebuilding every date touched since the last run:

```sql
{% if is_incremental() %}
where order_date in (
    select distinct order_date from {{ ref('orders') }}
    where _loaded_at > (select max(_loaded_at) from {{ this }})
)
{% endif %}
```

`daily_revenue` would also need a `max(_loaded_at) as _loaded_at` column for this to work. A scheduled periodic `--full-refresh` is another safety net.

## 3. A test that would have caught it

Add a reconciliation test, `tests/assert_daily_revenue_reconciles_to_orders.sql`:

```sql
-- daily_revenue must match orders for every order_date, in both directions
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
    expected.orders  as expected_orders,
    actual.orders    as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue   as actual_revenue

from expected

full outer join actual
    on expected.order_date = actual.order_date

where expected.orders is distinct from actual.orders
   or abs(coalesce(expected.revenue, 0) - coalesce(actual.revenue, 0)) > 0.005
```

Against last night's data this returns the six February dates and fails the build. It also catches the opposite problem: stale rows left in `daily_revenue` after orders are deleted or re-dated.

For a lighter alternative, `dbt_utils.equal_rowcount` between `orders` and `daily_revenue` won't work, because the grains differ. An `expression_is_true` check on totals (`sum(revenue)` vs `sum(amount)`) would catch the overall gap, but it won't tell you which dates are wrong.
