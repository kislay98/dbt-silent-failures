claude-haiku-5-5 | claude -p --setting-sources "" --model haiku --tools "" --output-format json | 29 s | USD 0.005 | 2026-10-14 | project bef6b69

## 1. Root cause

**`jaffle_shop/models/daily_revenue.sql`**, in the incremental filter. `orders` is correct: its February total is 485.00, which matches the settlement. `daily_revenue` is stale.

- The model is `incremental` with `delete+insert`, and on incremental runs it only rebuilds `order_date >= max(order_date) - 3 days` of the **table itself**. The table's max date is 2018-04-09, so the rebuild window starts around 2018-04-06. February is never recomputed after its first build.
- The `orders` table is rebuilt in full every run, so late payments reach it. `daily_revenue` never sees them for February.
- The comment "payments can land a day or two after the order" is the wrong assumption. These payments arrived weeks or months after their orders.
- The mismatch fits the late payments. In the `stg_payments` sample, payment 116 (8.0, order 36) is the 02-10 difference (55 vs 47). Payment 114 (5.0, order 30) and payment 115 (12.0, order 33) are the 02-02 (5) and 02-04 (12) differences. The remaining 45 is other late payments, which I inferred from the totals and did not check row by row.
- The row count matches (27 orders on both sides) because the late payments attach to orders that already exist. Only the amounts drift.
- No test compares `daily_revenue` to `orders`. Unique and not_null tests can't see a wrong sum, so the build passes.

## 2. Fix

**Immediate data repair:**
```
dbt run --select daily_revenue --full-refresh
```

**Code fix:** `daily_revenue` is a single `GROUP BY` over `orders`, and `orders` is already a full table each run. The incremental logic saves almost nothing and is the source of the drift, so I'd make it a table. Replace `jaffle_shop/models/daily_revenue.sql` with:

```sql
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

The config block goes, so the project default `+materialized: table` applies. Update the description in `jaffle_shop/models/schema.yml`, which still says "Incremental, rebuilds the last three days on each run".

If volume later makes a full rebuild too slow, keep incremental but key the rebuild on something that captures late payments (for example, a payments `updated_at`), not on `max(order_date)`. The raw payments table has no such column today.

## 3. Test that would have caught it

Add a singular test at `jaffle_shop/tests/assert_daily_revenue_matches_orders.sql`. It fails on any date where `daily_revenue` disagrees with `orders`, and it would have failed on the first run after the late payments landed:

```sql
with orders_by_day as (

    select
        order_date,
        count(*) as orders,
        sum(amount) as revenue

    from {{ ref('orders') }}

    group by order_date

)

select
    coalesce(o.order_date, d.order_date) as order_date,
    o.orders as orders_from_orders,
    d.orders as orders_from_daily,
    o.revenue as revenue_from_orders,
    d.revenue as revenue_from_daily

from orders_by_day o

full outer join {{ ref('daily_revenue') }} d
    on o.order_date = d.order_date

where o.orders is distinct from d.orders
   or abs(coalesce(o.revenue, 0) - coalesce(d.revenue, 0)) > 0.005
```

Any non-empty result fails the build, so this would also flag the existing February drift today.

**Unrelated, noticed while reading:** `jaffle_shop/models/schema.yml` documents `total_order_amount` on `customers`, but `customers.sql` produces `customer_lifetime_value`. The docs are out of date.

I haven't run any of this. The diagnosis and the date mapping come from the supplied outputs.
