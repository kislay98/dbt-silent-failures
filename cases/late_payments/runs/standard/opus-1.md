claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 29 s | USD 0.179 | 2026-10-14 | project ad0b16b | prompt 5e8b9b5cd1d1

# Feb 2018 revenue mismatch: stale incremental rows in `daily_revenue`

## 1. Root cause

The problem is in **`models/daily_revenue.sql`**, specifically its incremental filter. Some February payments in **`raw_payments`** arrived late, and `daily_revenue` never picked them up.

```sql
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
```

On each run, `daily_revenue` deletes and reinserts only the dates within 3 days of the **latest order date already in the table**. The window moves with order dates, not with when payments arrive.

What went wrong in the data:

- Some `raw_payments` rows for February orders were loaded after the table had already moved into March.
- For example, `raw_payments.id = 120` is for `order_id = 48`, which is a late-February order since order 50 is dated 2018-02-23. Its id is near the top of the 121 payment ids.
- When those rows landed, the February dates were outside the 3-day window, so they were never rebuilt.
- `orders` is a full `table` and recomputes everything each night, so it shows the settlement figure of 485.00.
- `daily_revenue` still holds the February rows built before those payments existed, so it shows 415.00. The 70.00 difference is the late payments.
- The order count matches (27 on both sides) because each order row already existed; only its payment total was partial.

The numbers confirm that `orders` is correct and `daily_revenue` is the outlier:

| Source | Total |
|---|---|
| `customers` lifetime value | 1742 |
| `orders`: 496 + 485 + 622 + 139 | 1742 |
| `daily_revenue` | 1672 (70 short, all in February) |

None of the tests catch this. Every test on `daily_revenue` checks a single column (unique/not_null), so a stale but non-null `revenue` passes.

To list the stale dates:

```sql
select o.order_date, o.revenue as orders_revenue, d.revenue as daily_revenue
from (select order_date, sum(amount) revenue from orders group by 1) o
join daily_revenue d using (order_date)
where o.revenue is distinct from d.revenue
order by 1;
```

## 2. Fix

**Repair the data now:**

```bash
dbt build --select daily_revenue --full-refresh
```

**Fix the code so it can't happen again.** `raw_payments` has no load timestamp, so the model cannot tell which dates received new payments. A fixed lookback window will always miss some late payment. The table is 69 rows built from a 99-row table, so being incremental gains nothing. Make it a plain table:

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

If it ever has to be incremental, have ingestion add a `loaded_at` column to `raw_payments`. Then rebuild every `order_date` that has a payment with `loaded_at > (select max(_loaded_at) from {{ this }})`, instead of using a window based on order date.

## 3. Test that would have caught it

Add a reconciliation test as `tests/assert_daily_revenue_reconciles_to_orders.sql`:

```sql
-- daily_revenue must match orders for every order_date
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
where expected.orders is distinct from actual.orders
   or abs(coalesce(expected.revenue, 0) - coalesce(actual.revenue, 0)) > 0.005
```

On last night's build this test would have failed and returned the February dates, 70.00 short in total, before finance found the gap.
