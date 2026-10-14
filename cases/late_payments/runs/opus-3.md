claude-opus-5-5 | claude -p --setting-sources "" --model opus --tools "" --output-format json | 36 s | USD 0.146 | 2026-10-14 | project f769fdf | prompt e8c77caade40

# February revenue: `daily_revenue` never picked up late payments

## 1. Root cause

**The bug is in `models/daily_revenue.sql`, the incremental lookback. The trigger is late-arriving rows in `raw_payments`.**

- **The data:** the ingestion job loaded extra payments (ids 114, 115, 116, …) for orders placed in February. These are second payments that arrived after the order's first payment:
  - Payment 114 adds 5.00 to order 30.
  - Payment 115 adds 12.00 to order 33.
  - Payment 116 adds 8.00 to order 36.
- **Where they show up:** `orders` is a full `table` rebuild, so it includes these payments. Its February total is 485.00, which matches the settlement.
- **Where they don't:** `daily_revenue` is incremental and only reprocesses `order_date >= max(order_date) - 3 days` from itself. Its last day is 2018-04-09, so each run only rebuilds 2018-04-06 to 2018-04-09. February rows were written before the late payments existed and are never touched again.
- **Why the order count still matches:** the order counts were already correct (27), so only revenue is stale.

The numbers confirm this. The per-day gaps are 5, 12, 8, 15, 6, 9, 11 and 4:

| Date | Gap | Payment |
|---|---|---|
| 02-02 | 5 | id 114 |
| 02-04 | 12 | id 115 |
| 02-10 | 8 | id 116 |
| others | 15, 6, 9, 11, 4 | presumably later ids |

- These gaps sum to **70.00**, which is exactly 485 − 415.
- 70.00 is also the whole-table gap (1742 − 1672), so all of the drift is in February. That is why January and March agree.

The 3-day window assumes late payments arrive within 3 days *of the latest order date in the table*. That assumption does not hold here. With no load timestamp on `raw_payments`, the model has no way to notice that old dates changed.

Why every test passed: nothing compares `daily_revenue` to `orders`. `unique` and `not_null` on stale rows still pass.

## 2. Fix

**Immediate:** rebuild the table so February closes correctly.

```bash
dbt build --select daily_revenue --full-refresh
```

**Durable:** stop trusting a fixed date window. The aggregate is tiny, so the simplest option is `materialized='table'`. If you want to keep it incremental, recompute every day and write only the days that are new or have changed:

```sql
{{
    config(
        materialized='incremental',
        unique_key='order_date',
        incremental_strategy='delete+insert'
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

{% if is_incremental() %}
-- payments can land long after the order, so rewrite any day whose totals changed
select final.*
from final
left join {{ this }} as existing
    on final.order_date = existing.order_date
where existing.order_date is null
   or final.orders is distinct from existing.orders
   or final.revenue is distinct from existing.revenue
{% else %}
select * from final
{% endif %}
```

One limitation: a day whose orders all disappear would not be deleted. That case needs a full refresh.

Longer term, have ingestion add a `_loaded_at` column to `raw_payments`. Then the incremental filter can select the order dates of payments loaded since the last run, instead of guessing a window.

## 3. Test that would have caught it

Add a reconciliation test at `tests/assert_daily_revenue_reconciles_to_orders.sql`:

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
    coalesce(expected.order_date, actual.order_date) as order_date,
    expected.orders  as expected_orders,
    actual.orders    as actual_orders,
    expected.revenue as expected_revenue,
    actual.revenue   as actual_revenue
from expected
full outer join {{ ref('daily_revenue') }} as actual
    on expected.order_date = actual.order_date
where expected.orders is distinct from actual.orders
   or abs(coalesce(expected.revenue, 0) - coalesce(actual.revenue, 0)) > 0.005
```

On the nightly run above, this test would have failed with 8 rows (the February dates listed in the table). That would have flagged the problem before finance did.

---

Two things I noticed that are unrelated to this issue:

- `raw_customer_regions` has an `updated_at` column but no `unique` test on `customer_id`. If the regions feed ever sends history rows, `region_revenue` will fan out and overcount.
- `schema.yml` documents a `total_order_amount` column on `customers`, but the model outputs `customer_lifetime_value` instead.
