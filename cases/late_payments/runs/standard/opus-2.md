claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 29 s | USD 0.181 | 2026-10-14 | project ad0b16b | prompt 5e8b9b5cd1d1

# daily_revenue: stale February revenue from late-arriving payments

## 1. Root cause

**File: `models/daily_revenue.sql`**, specifically the incremental filter:

```sql
{% if is_incremental() %}
where order_date >= (select max(order_date) - interval 3 day from {{ this }})
{% endif %}
```

`orders.amount` is the sum of payments joined to the order. The model's comment assumes every payment lands within three days of its order, so it only rebuilds the last three days. The data breaks that assumption: **`raw_payments` contains payments for February orders that were loaded after February had left the three-day window.**

The output shows this:

- `raw_payments` has 121 rows for 99 orders, and the highest ids belong to old orders. Payment `id 120` (1100 cents) is for `order_id 48`, a late-February order. Order 50 is dated 2018-02-23, so order 48 sits just before it. The ingestion job appended it long after the order date.
- `orders` is a full `table` rebuild, so it picks up those payments: February is 485.00, which matches the settlement.
- `daily_revenue` was built while those February days were still in the window, before the late payments arrived. Those days have never been rebuilt since, because the window is anchored on `max(order_date)`, which is now in April. The rows keep their old, partial revenue.
- The order count still matches (27 = 27) because the orders existed when those days were built; only their payments were incomplete. The missing 70.00 is the total of late payments on February orders.
- Every test passes because `not_null` and `unique` on `daily_revenue` can't detect a stale value. Nothing compares `daily_revenue` to `orders`.

To see exactly which days are stale:

```sql
with o as (select order_date, count(*) n, coalesce(sum(amount),0) rev from orders group by 1)
select o.order_date, o.n, d.orders, o.rev, d.revenue, o.rev - d.revenue as missing
from o join daily_revenue d using (order_date)
where o.rev <> d.revenue
order by 1;
```

The `missing` column should add up to 70.00, all in February.

## 2. Fix

**Immediate data repair** (rebuilds every day from `orders`):

```bash
dbt build --select daily_revenue --full-refresh
```

**Code fix:** a fixed lookback window can't be correct here. `raw_payments` has no load timestamp, so the model can't tell which order dates received new payments. The table has 69 rows built from a 99-row `orders` table, so incremental gives no real benefit. I recommend making it a plain table:

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

If it has to stay incremental later, the ingestion job would need to add a `loaded_at` column to `raw_payments`. The filter would then rebuild every `order_date` that has an order with a payment loaded since the last run, rather than using a fixed number of days. Also update the description in `schema.yml`, which still says "rebuilds the last three days."

## 3. Test that would have caught it

Add a reconciliation test, `tests/assert_daily_revenue_reconciles_to_orders.sql`:

```sql
-- daily_revenue must agree with orders for every order_date (count and revenue)
with expected as (

    select
        order_date,
        count(*) as orders,
        coalesce(sum(amount), 0) as revenue
    from {{ ref('orders') }}
    group by order_date

),

actual as (

    select
        order_date,
        orders,
        coalesce(revenue, 0) as revenue
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
   or abs(expected.revenue - actual.revenue) > 0.005
```

`dbt build` runs this test after `daily_revenue` builds. On the current data it would have failed and listed the February dates with missing revenue. The full outer join also catches days missing from either side.
