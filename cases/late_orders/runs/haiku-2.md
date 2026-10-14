claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 26 s | USD 0.004 | 2026-10-14 | project 7d5b525+dirty

## 1. Root cause

**`jaffle_shop/models/daily_revenue.sql`** is an incremental model that only rebuilds recent dates, so orders that show up late for earlier dates are never inserted.

The model's incremental filter is:

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

The lower bound comes from the target table's own `max(order_date)`, not from anything in the source. On each run, dbt deletes and reinserts only the rows from the last three days already in `daily_revenue`. Any order whose `order_date` is older than that window is never read again, even if it has since appeared in `orders`.

The data confirms this. The six missing orders are all in February, the table's latest date is in April, and the `delete+insert` never touches February:

| Date | In `orders` | In `daily_revenue` |
|---|---|---|
| 2018-02-03 | 1 | 0 |
| 2018-02-08 | 2 | 1 |
| 2018-02-13 | 2 | 1 |
| 2018-02-17 | 4 | 3 |
| 2018-02-22 | 1 | 0 |
| 2018-02-26 | 3 | 2 |

The six orders account for the whole gap: 33 vs 27 orders and 480 vs 415 revenue for February, and 105 vs 99 orders and 1737 vs 1672 overall (65 short, same amount).

The model's comment is also misleading. It says payments land late, but the filter works on order dates, and the rows that arrived late here are orders. Nothing in the source (`raw_orders` is a seed with only `ordered_at`, no load timestamp) lets the model detect late arrivals, so the three-day window can't catch them.

Nothing failed because the tests only check that `order_date` is unique and not null, and that the revenue and orders columns are not null. None of them compare `daily_revenue` with `orders`.

I can't confirm from this sample exactly when those six orders were added to the seed. Checking the seed's change history for those six `raw_orders` ids would show that.

## 2. Fix

**Immediate data fix** (rebuilds the table from `orders`, which is correct):

```bash
dbt run --select daily_revenue --full-refresh
```

**Code fix.** The table is small (one row per day), so the incremental logic isn't buying much. Materialize it as a table so every run recomputes from `orders`:

```sql
{{
    config(
        materialized='table'
    )
}}

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

If you need incremental for scale later, the watermark has to come from something that records when a row arrived (a load timestamp on `raw_orders`), not from the target's max date.

Also remove the `unique_key` and `delete+insert` config, which only make sense for the incremental version.

## 3. Test that would have caught it

Add a singular test that compares `daily_revenue` with `orders` day by day, in `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`:

```sql
with expected as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

)

select
    coalesce(e.order_date, d.order_date) as order_date,
    e.orders as expected_orders,
    d.orders as actual_orders,
    e.revenue as expected_revenue,
    d.revenue as actual_revenue

from expected e

full outer join {{ ref('daily_revenue') }} d
    on e.order_date = d.order_date

where coalesce(e.orders, 0) <> coalesce(d.orders, 0)
   or abs(coalesce(e.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Run against the current data, this returns the six February dates and fails the build. The `> 0.005` tolerance avoids false failures from floating-point differences in the summed revenue.

**Other things I noticed, not related to this bug:**
- `region_revenue` joins on `raw_customer_regions`, which has no unique test on `customer_id`. A customer with two regions would double-count revenue.
- `schema.yml` documents `total_order_amount` on `customers`, but `customers.sql` doesn't produce that column.
